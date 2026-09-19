"""Durable job queue built on the existing ``jobs`` table.

The web process only enqueues work; a separate worker claims it. Claims are
compare-and-set updates, so two workers can never run the same job, and each claim
carries a lease that a heartbeat renews. When a worker dies, the lease expires and
another worker reclaims the job: the unit-level CAS plus the stage cache mean already
finished stages are reused instead of being paid for twice.

Timestamps for the lease are stored as naive UTC because SQLite compares them as
strings; they are queue bookkeeping only and are converted back to aware UTC on read.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.storage.database import Database, jobs

QUEUED = "queued"
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
COMPLETED_WITH_ERRORS = "completed_with_errors"
BLOCKED = "blocked"
FAILED = "failed"
CANCELLED = "cancelled"

#: Statuses a worker may claim.
CLAIMABLE_STATUSES = frozenset({QUEUED})
#: Statuses that mean "somebody still intends to finish this job".
ACTIVE_STATUSES = frozenset({QUEUED, RUNNING, PAUSED, BLOCKED})
#: Statuses where the runner updates the job itself rather than the worker.
TERMINAL_STATUSES = frozenset({COMPLETED, COMPLETED_WITH_ERRORS, FAILED, CANCELLED})

READING_KIND = "reading_generation"
SAMPLE_KIND = "reading_sample"
NOVEL_KIND = "novel_component"

#: Statuses a worker's heartbeat may extend a lease for. A paused job is still owned by
#: the worker that is finishing its current stage, so its lease keeps being renewed.
LEASABLE_STATUSES = frozenset({QUEUED, RUNNING, PAUSED})


def utcnow() -> dt.datetime:
    """Aware UTC timestamp, the single source of 'now' inside the queue."""

    return dt.datetime.now(dt.UTC)


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _store(value: dt.datetime) -> dt.datetime:
    """Naive UTC for storage so SQLite string comparison stays chronological."""

    return value.astimezone(dt.UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class QueueJob:
    """One row of the queue, as the worker and the UI need to see it."""

    id: str
    corpus_id: str
    status: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    worker_id: str | None
    lease_expires_at: dt.datetime | None
    heartbeat_at: dt.datetime | None
    error_code: str | None
    updated_at: dt.datetime | None

    @property
    def lease_expired(self) -> bool:
        expires = _as_utc(self.lease_expires_at)
        return expires is not None and expires <= utcnow()

    def waiting_reason(self) -> str:
        """Human-readable queue state for the web UI."""

        if self.status == QUEUED:
            return "等待 worker"
        if self.status == RUNNING and self.lease_expired:
            return "中断待恢复"
        if self.status == RUNNING:
            return "worker 处理中"
        if self.status == PAUSED:
            return "已暂停"
        if self.status == BLOCKED:
            return "已阻塞（检查密钥或余额）"
        if self.status == CANCELLED:
            return "已取消"
        if self.status == FAILED:
            return "已失败"
        if self.status == COMPLETED:
            return "已完成"
        if self.status == COMPLETED_WITH_ERRORS:
            return "完成但有失败单元"
        return self.status


def _to_job(row: Any) -> QueueJob:
    return QueueJob(
        id=row["id"],
        corpus_id=row["corpus_id"],
        status=row["status"],
        kind=row["kind"],
        payload=json.loads(row["payload"]),
        attempts=row["attempts"],
        worker_id=row["worker_id"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        error_code=row["error_code"],
        updated_at=row["updated_at"],
    )


def new_worker_id(prefix: str = "worker") -> str:
    """Identifier for one worker process; also the lease owner recorded in the row."""

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class QueueLimitError(RuntimeError):
    """Raised when accepting another job would exceed the global active-job cap."""


class JobQueue:
    """Queue operations the web process and the worker share."""

    def __init__(self, database: Database, *, lease_seconds: int = 300) -> None:
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30 seconds")
        self.database = database
        self.lease_seconds = lease_seconds

    # ---------------------------------------------------------------- enqueue
    def enqueue(
        self,
        *,
        corpus_id: str,
        payload: dict[str, Any] | None = None,
        kind: str = READING_KIND,
        job_id: str | None = None,
        status: str = QUEUED,
        idempotency_key: str | None = None,
    ) -> QueueJob:
        """Insert a job, reusing the existing row when the idempotency key repeats."""

        if idempotency_key:
            existing = self.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.status not in TERMINAL_STATUSES:
                    return existing
                # The previous run finished: free the key so this request can start a
                # fresh job instead of being folded into the old one.
                self.clear_idempotency_key(existing.id)
        identifier = job_id or str(uuid.uuid4())
        values = {
            "id": identifier,
            "corpus_id": corpus_id,
            "status": status,
            "kind": kind,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
            "idempotency_key": idempotency_key,
        }
        try:
            with self.database.engine.begin() as connection:
                connection.execute(insert(jobs).values(**values))
        except IntegrityError:
            # Two requests raced with the same key: the unique index picked a winner.
            if not idempotency_key:
                raise
            existing = self.find_by_idempotency_key(idempotency_key)
            if existing is None:  # pragma: no cover - the winner must be visible now
                raise
            return existing
        job = self.get(identifier)
        if job is None:  # pragma: no cover - the insert just happened
            raise RuntimeError("job disappeared right after insert")
        return job

    def find_by_idempotency_key(self, key: str) -> QueueJob | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(jobs).where(jobs.c.idempotency_key == key).limit(1))
                .mappings()
                .one_or_none()
            )
        return _to_job(row) if row is not None else None

    def find_active_by_key(self, key: str) -> QueueJob | None:
        """The still-running job for this key, if any: the double-click guard."""

        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(jobs)
                    .where(jobs.c.idempotency_key == key, jobs.c.status.in_(ACTIVE_STATUSES))
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        return _to_job(row) if row is not None else None

    def clear_idempotency_key(self, job_id: str) -> bool:
        """Release a finished job's key so the same action can be requested again."""

        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(jobs).where(jobs.c.id == job_id).values(idempotency_key=None)
            )
        return result.rowcount == 1

    def running_count(self) -> int:
        """Jobs that are queued or running, i.e. the ones that will spend tokens."""

        counts = self.counts()
        return counts.get(QUEUED, 0) + counts.get(RUNNING, 0)

    def get(self, job_id: str) -> QueueJob | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(jobs).where(jobs.c.id == job_id))
                .mappings()
                .one_or_none()
            )
        return _to_job(row) if row is not None else None

    # ------------------------------------------------------------- claim/lease
    def claim(self, worker_id: str, *, kinds: tuple[str, ...] | None = None) -> QueueJob | None:
        """Atomically take the oldest queued job; ``None`` when nothing is claimable.

        The ``status = queued`` predicate inside the UPDATE is what makes the claim
        exclusive: a second worker that read the same candidate updates zero rows and is
        told to retry.
        """

        expires = _store(utcnow() + dt.timedelta(seconds=self.lease_seconds))
        with self.database.engine.begin() as connection:
            candidate = select(jobs.c.id).where(jobs.c.status.in_(CLAIMABLE_STATUSES))
            if kinds:
                candidate = candidate.where(jobs.c.kind.in_(kinds))
            candidate = candidate.order_by(jobs.c.created_at, jobs.c.id).limit(1)
            row = connection.execute(candidate).first()
            if row is None:
                return None
            claimed = connection.execute(
                update(jobs)
                .where(jobs.c.id == row.id, jobs.c.status == QUEUED)
                .values(
                    status=RUNNING,
                    worker_id=worker_id,
                    lease_expires_at=expires,
                    heartbeat_at=_store(utcnow()),
                    attempts=jobs.c.attempts + 1,
                    error_code=None,
                    updated_at=func.now(),
                )
            )
            if claimed.rowcount != 1:
                return None
        return self.get(row.id)

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """Extend the lease of a job this worker still owns."""

        now = utcnow()
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.worker_id == worker_id,
                    jobs.c.status.in_(LEASABLE_STATUSES),
                )
                .values(
                    heartbeat_at=_store(now),
                    lease_expires_at=_store(now + dt.timedelta(seconds=self.lease_seconds)),
                    updated_at=func.now(),
                )
            )
        return result.rowcount == 1

    def release(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Give the job back: clear the lease, optionally forcing a final status.

        ``status=None`` keeps whatever the runner decided (completed /
        completed_with_errors / paused / cancelled) and only drops the lease.
        """

        values: dict[str, Any] = {
            "worker_id": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "updated_at": func.now(),
        }
        if status is not None:
            values["status"] = status
        if error_code is not None:
            values["error_code"] = error_code
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.worker_id == worker_id)
                .values(**values)
            )
        return result.rowcount == 1

    def reclaim_expired(self, *, max_attempts: int = 3) -> list[str]:
        """Requeue jobs whose worker disappeared; fail the ones that keep dying."""

        now = _store(utcnow())
        reclaimed: list[str] = []
        with self.database.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(jobs.c.id, jobs.c.attempts).where(
                        jobs.c.status == RUNNING,
                        jobs.c.lease_expires_at.is_not(None),
                        jobs.c.lease_expires_at < now,
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                exhausted = row["attempts"] >= max_attempts
                connection.execute(
                    update(jobs)
                    .where(jobs.c.id == row["id"], jobs.c.status == RUNNING)
                    .values(
                        status=FAILED if exhausted else QUEUED,
                        worker_id=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        error_code="worker_lease_expired",
                        updated_at=func.now(),
                    )
                )
                reclaimed.append(row["id"])
        return reclaimed

    # ------------------------------------------------------------ user actions
    def request_pause(self, job_id: str) -> bool:
        """Stop claiming new units; an in-flight stage finishes and saves first."""

        return self._set_status(job_id, {QUEUED, RUNNING}, PAUSED)

    def resume(self, job_id: str) -> bool:
        """Hand a paused or blocked job back to the queue."""

        return self._set_status(job_id, {PAUSED, BLOCKED}, QUEUED)

    def cancel(self, job_id: str) -> bool:
        return self._set_status(
            job_id,
            {QUEUED, RUNNING, PAUSED, BLOCKED},
            CANCELLED,
            extra={"worker_id": None, "lease_expires_at": None, "heartbeat_at": None},
        )

    def _set_status(
        self,
        job_id: str,
        expected: set[str],
        target: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        values: dict[str, Any] = {"status": target, "updated_at": func.now()}
        values.update(extra or {})
        expected_statuses = sorted(expected)
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.status.in_(expected_statuses))
                .values(**values)
            )
        return result.rowcount == 1

    # -------------------------------------------------------------- reporting
    def counts(self) -> dict[str, int]:
        """Job counts per status, for the operator page and the UI badges."""

        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(jobs.c.status, func.count()).group_by(jobs.c.status)
            ).all()
        return {status: count for status, count in rows}

    def active_count(self) -> int:
        """Jobs that still intend to run; used to enforce the global cap."""

        return sum(count for status, count in self.counts().items() if status in ACTIVE_STATUSES)

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[QueueJob]:
        statement = select(jobs).order_by(jobs.c.created_at.desc()).limit(limit)
        if status is not None:
            statement = statement.where(jobs.c.status == status)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_to_job(row) for row in rows]

