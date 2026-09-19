"""Queue mechanics: exclusive claims, leases, lifecycle and idempotency.

These tests use a real SQLite database because the guarantees come from SQL
compare-and-set semantics, not from Python-level locking.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.models import Corpus
from app.pipeline.queue import (
    CANCELLED,
    COMPLETED,
    PAUSED,
    QUEUED,
    RUNNING,
    JobQueue,
    new_worker_id,
)
from app.storage.database import Database, jobs
from app.storage.repositories import Repository


def make_queue(tmp_path: Path, *, lease_seconds: int = 300) -> tuple[JobQueue, Database]:
    database = Database(tmp_path / "state.db")
    database.migrate()
    Repository(database).add_corpus(
        Corpus(
            id="corpus-1",
            name="Corpus",
            source_path="source.txt",
            source_hash="hash",
            format="txt",
            chapter_count=1,
            parser_version="1",
        )
    )
    return JobQueue(database, lease_seconds=lease_seconds), database


def expire_lease(database: Database, job_id: str) -> None:
    """Pretend the owning worker stopped: its lease is now in the past."""

    past = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(hours=1)
    with database.engine.begin() as connection:
        connection.execute(
            jobs.update().where(jobs.c.id == job_id).values(lease_expires_at=past)
        )


def test_claim_is_exclusive_when_two_workers_race(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", payload={"unit_ids": ["u1"]})

    def race(_: int) -> str | None:
        worker = new_worker_id()
        claimed = queue.claim(worker)
        return claimed.id if claimed is not None else None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(race, range(4)))

    assert results.count(None) == 3
    assert len([value for value in results if value is not None]) == 1
    assert queue.counts().get(RUNNING) == 1


def test_claim_returns_jobs_in_creation_order_and_stops_when_empty(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    first = queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    second = queue.enqueue(corpus_id="corpus-1", job_id="job-2")

    assert queue.claim(new_worker_id()).id == first.id
    assert queue.claim(new_worker_id()).id == second.id
    assert queue.claim(new_worker_id()) is None


def test_heartbeat_extends_the_lease_for_the_owner_only(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    owner = new_worker_id()
    other = new_worker_id()
    claimed = queue.claim(owner)

    assert queue.heartbeat("job-1", other) is False
    assert queue.heartbeat(claimed.id, owner) is True
    refreshed = queue.get("job-1")
    assert refreshed.worker_id == owner
    assert refreshed.lease_expired is False


def test_expired_lease_is_reclaimed_and_finished_by_another_worker(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    dead = new_worker_id()
    queue.claim(dead)
    expire_lease(database, "job-1")

    reclaimed = queue.reclaim_expired(max_attempts=3)

    assert reclaimed == ["job-1"]
    requeued = queue.get("job-1")
    assert requeued.status == QUEUED
    assert requeued.worker_id is None

    survivor = new_worker_id()
    taken = queue.claim(survivor)

    assert taken.id == "job-1"
    assert taken.attempts == 2  # the crashed attempt is still recorded
    assert taken.worker_id == survivor


def test_repeated_crashes_fail_the_job_instead_of_looping(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    queue.claim(new_worker_id())
    with database.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.id == "job-1").values(attempts=3))
    expire_lease(database, "job-1")

    assert queue.reclaim_expired(max_attempts=3) == ["job-1"]

    failed = queue.get("job-1")
    assert failed.status == "failed"
    assert failed.error_code == "worker_lease_expired"


def test_pause_resume_cancel_lifecycle(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")

    assert queue.request_pause("job-1") is True
    assert queue.get("job-1").status == PAUSED
    # A paused job is not claimable, but it can be resumed.
    assert queue.claim(new_worker_id()) is None

    assert queue.resume("job-1") is True
    owner = new_worker_id()
    assert queue.claim(owner).status == RUNNING

    # Cancelling clears the lease so no worker keeps renewing it.
    assert queue.cancel("job-1") is True
    cancelled = queue.get("job-1")
    assert cancelled.status == CANCELLED
    assert cancelled.worker_id is None and cancelled.lease_expires_at is None
    assert queue.cancel("job-1") is False
    assert queue.resume("job-1") is False


def test_pause_is_possible_while_a_worker_runs_the_job(tmp_path: Path) -> None:
    """Pausing must work mid-run: the runner stops at the next unit boundary."""

    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    queue.claim(new_worker_id())

    assert queue.request_pause("job-1") is True
    assert queue.get("job-1").status == PAUSED
    # The lease is still owned, so a heartbeat keeps working until the runner returns.
    assert queue.heartbeat("job-1", queue.get("job-1").worker_id) is True


def test_release_keeps_the_status_the_runner_decided(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    owner = new_worker_id()
    queue.claim(owner)

    assert queue.release("job-1", owner, status=COMPLETED) is True

    finished = queue.get("job-1")
    assert finished.status == COMPLETED
    assert finished.worker_id is None and finished.lease_expires_at is None
    # A different worker can no longer touch it.
    assert queue.release("job-1", new_worker_id(), status=COMPLETED) is False


def test_idempotency_key_reuses_the_running_job(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)

    first = queue.enqueue(corpus_id="corpus-1", job_id="job-1", idempotency_key="key-1")
    again = queue.enqueue(corpus_id="corpus-1", job_id="job-2", idempotency_key="key-1")

    assert again.id == first.id
    assert queue.counts().get(QUEUED) == 1


def test_idempotency_key_is_released_once_the_job_finished(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1", idempotency_key="key-1")
    with database.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.id == "job-1").values(status=COMPLETED))

    fresh = queue.enqueue(corpus_id="corpus-1", job_id="job-2", idempotency_key="key-1")

    assert fresh.id == "job-2"
    assert queue.get("job-1").status == COMPLETED


def test_counts_report_every_status_including_the_active_subset(tmp_path: Path) -> None:
    queue, _database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="queued-1")
    queue.enqueue(corpus_id="corpus-1", job_id="paused-1")
    queue.request_pause("paused-1")
    queue.enqueue(corpus_id="corpus-1", job_id="running-1")
    queue.claim(new_worker_id())

    counts = queue.counts()

    assert counts == {"queued": 1, "paused": 1, "running": 1}
    # Only queued + running jobs will spend tokens, and that is the number the cap uses.
    assert queue.running_count() == 2
