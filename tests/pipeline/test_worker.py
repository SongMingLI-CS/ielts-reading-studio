"""Worker behaviour: claiming, heartbeats, structured failures and crash recovery.

The first half drives the worker with fake handlers (no pipeline, no provider); the last
test runs a real reading job through the worker with the deterministic fake provider.
"""

from __future__ import annotations

import threading
from pathlib import Path

from app.config import AppConfig
from app.models import Difficulty
from app.pipeline.service import ReadingStudioService
from app.pipeline.worker import JobWorker
from app.planning.units import default_question_types
from tests.fixtures.valid_generation import DeterministicProvider
from tests.pipeline.test_queue import expire_lease, make_queue


class FakeService:
    """The worker only needs a database and (for real jobs) a run_job method."""

    def __init__(self, database) -> None:
        self.database = database


def test_worker_runs_a_job_and_clears_the_lease(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1", payload={"unit_ids": ["u1"]})
    handled: list[str] = []

    def handler(context) -> None:
        handled.append(context.job.id)
        assert context.job.payload == {"unit_ids": ["u1"]}

    worker = JobWorker(
        FakeService(database), queue=queue, handlers={"reading_generation": handler}
    )

    finished = worker.run_once()

    assert handled == ["job-1"]
    assert finished.id == "job-1"
    assert finished.status == "completed"
    assert finished.attempts == 1
    assert finished.worker_id is None and finished.lease_expires_at is None


def test_worker_records_a_structured_error_code_when_the_handler_fails(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")

    def handler(_context) -> None:
        raise RuntimeError("provider exploded")

    worker = JobWorker(
        FakeService(database), queue=queue, handlers={"reading_generation": handler}
    )

    finished = worker.run_once()

    assert finished.status == "failed"
    assert finished.error_code == "handler_failed:RuntimeError"
    assert finished.worker_id is None


def test_worker_does_not_finish_a_job_the_user_paused_mid_run(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")

    def handler(context) -> None:
        # The runner stops at a unit boundary, which the worker must respect.
        context.queue.request_pause(context.job.id)

    worker = JobWorker(
        FakeService(database), queue=queue, handlers={"reading_generation": handler}
    )

    finished = worker.run_once()

    assert finished.status == "paused"
    assert finished.worker_id is None


def test_worker_reclaims_a_job_whose_previous_worker_died(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")

    # Worker A claims the job and disappears without releasing the lease.
    queue.claim("worker-a")
    expire_lease(database, "job-1")

    handled: list[str] = []
    worker = JobWorker(
        FakeService(database),
        queue=queue,
        handlers={"reading_generation": lambda context: handled.append(context.job.id)},
    )

    finished = worker.run_once()

    assert handled == ["job-1"]
    assert finished.status == "completed"
    assert finished.attempts == 2


def test_run_forever_returns_immediately_when_stopped(tmp_path: Path) -> None:
    queue, database = make_queue(tmp_path)
    queue.enqueue(corpus_id="corpus-1", job_id="job-1")
    stop = threading.Event()
    stop.set()
    worker = JobWorker(
        FakeService(database), queue=queue, handlers={"reading_generation": lambda _c: None}
    )

    assert worker.run_forever(stop_event=stop) == 0
    # Nothing was claimed: the job is still queued for the next worker.
    assert queue.get("job-1").status == "queued"


def test_worker_executes_a_real_reading_job_with_a_fake_provider(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text(
        "\n".join(f"第{index}章\n" + "正文。" * 200 for index in range(1, 6)),
        encoding="utf-8",
    )
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    provider = DeterministicProvider()
    studio = ReadingStudioService(config, provider=provider)
    corpus = studio.import_source(source).corpus
    sample = studio.generate_sample(
        corpus.id, Difficulty.STANDARD, default_question_types(Difficulty.STANDARD)
    )
    studio.approve_sample(corpus.id, sample.unit_id)
    job = studio.create_job(corpus.id, ordinals=[2, 3])
    provider.requests.clear()

    finished = JobWorker(studio).run_once()

    assert finished.id == job["id"]
    assert finished.status in {"completed", "completed_with_errors"}
    assert len(provider.requests) >= 4
    assert finished.worker_id is None
    assert all(
        unit.status.value == "completed" for unit in studio.repository.list_job_units(job["id"])
    )
