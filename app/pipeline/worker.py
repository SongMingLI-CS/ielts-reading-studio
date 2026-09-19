"""Durable job execution: a worker process that owns the long generations.

FastAPI ``BackgroundTasks`` die with the web process, so a restart in the middle of a
40-minute batch used to leave the job stuck with no way to continue. The worker instead
claims jobs through :class:`~app.pipeline.queue.JobQueue` (atomic compare-and-set plus a
lease), renews the lease while it works, and carries on after a restart because every
piece of progress the pipeline needs (unit status, stage attempts, cache keys) already
lives in SQLite.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.pipeline.queue import (
    COMPLETED,
    FAILED,
    NOVEL_KIND,
    PAUSED,
    READING_KIND,
    SAMPLE_KIND,
    TERMINAL_STATUSES,
    JobQueue,
    QueueJob,
    new_worker_id,
)

LOGGER = logging.getLogger("app.worker")

#: Job kinds this worker knows how to execute.
Handler = Callable[["JobContext"], Any]


def reading_generation_handler(context: JobContext) -> Any:
    """Run one reading batch; the runner itself owns per-unit resume and caching."""

    return context.service.run_job(context.job.id)


def novel_component_handler(context: JobContext) -> Any:
    """Run one context-novel batch as a subprocess of the component CLI."""

    from app.pipeline.novel_runner import run_novel_job

    return run_novel_job(context.service, context.job.payload)


def reading_sample_handler(context: JobContext) -> Any:
    """Generate exactly one sample unit for manual approval."""

    from app.pipeline.tasks import run_sample_task

    return run_sample_task(context.service, context.job.payload)


class Heartbeat:
    """Renews a job lease in a background thread while the handler runs."""

    def __init__(
        self,
        queue: JobQueue,
        job_id: str,
        worker_id: str,
        *,
        interval: float | None = None,
    ) -> None:
        self.queue = queue
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval = interval if interval is not None else max(10.0, queue.lease_seconds / 4)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def _beat(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if not self.queue.heartbeat(self.job_id, self.worker_id):
                    # The lease is gone: someone else may have taken over, so stop
                    # pretending this worker still owns the job.
                    self.lost = True
                    return
            except Exception:
                LOGGER.warning("心跳失败 job_id=%s", self.job_id, exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._beat, name=f"heartbeat-{self.job_id[:8]}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1)


@dataclass
class JobContext:
    """Everything a handler may touch."""

    job: QueueJob
    queue: JobQueue
    service: Any
    heartbeat: Heartbeat


def default_handlers() -> dict[str, Handler]:
    return {
        READING_KIND: reading_generation_handler,
        SAMPLE_KIND: reading_sample_handler,
        NOVEL_KIND: novel_component_handler,
    }


class JobWorker:
    """Claims queued jobs one at a time and finishes them unattended."""

    def __init__(
        self,
        service: Any,
        *,
        queue: JobQueue | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        poll_interval: float = 2.0,
        kinds: tuple[str, ...] | None = None,
        handlers: dict[str, Handler] | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.service = service
        self.queue = queue or JobQueue(service.database, lease_seconds=lease_seconds)
        self.worker_id = worker_id or new_worker_id()
        self.poll_interval = poll_interval
        self.handlers = handlers or default_handlers()
        # Claim every kind this worker can actually execute.
        self.kinds = kinds or tuple(self.handlers)
        self.max_attempts = max_attempts
        # One job at a time per process: this is the global model-call ceiling, on top of
        # the per-job `concurrency` the batch runner already honours.
        self.active_job_id: str | None = None

    def reclaim_stale(self) -> list[str]:
        """Return crashed workers' jobs to the queue (or fail them after N attempts)."""

        reclaimed = self.queue.reclaim_expired(max_attempts=self.max_attempts)
        for job_id in reclaimed:
            LOGGER.warning("回收过期租约的作业 job_id=%s", job_id)
        return reclaimed

    def run_once(self) -> QueueJob | None:
        """Claim and execute at most one job; returns the finished job, if any."""

        self.reclaim_stale()
        job = self.queue.claim(self.worker_id, kinds=self.kinds)
        if job is None:
            return None
        handler = self.handlers.get(job.kind)
        if handler is None:
            LOGGER.error("未知作业类型 kind=%s job_id=%s", job.kind, job.id)
            self.queue.release(job.id, self.worker_id, status=FAILED, error_code="unknown_job_kind")
            return self.queue.get(job.id)

        heartbeat = Heartbeat(self.queue, job.id, self.worker_id)
        self.active_job_id = job.id
        error_code: str | None = None
        LOGGER.info(
            "开始作业 job_id=%s kind=%s attempt=%s", job.id, job.kind, job.attempts
        )
        heartbeat.start()
        try:
            handler(JobContext(job=job, queue=self.queue, service=self.service, heartbeat=heartbeat))
        except Exception as exc:
            error_code = f"handler_failed:{type(exc).__name__}"
            LOGGER.exception("作业失败 job_id=%s error_code=%s", job.id, error_code)
        finally:
            heartbeat.stop()
            self.active_job_id = None

        self._finish(job.id, error_code=error_code, lease_lost=heartbeat.lost)
        return self.queue.get(job.id)

    def _finish(self, job_id: str, *, error_code: str | None, lease_lost: bool) -> None:
        """Drop the lease without overwriting a decision the runner already made."""

        current = self.queue.get(job_id)
        if current is None:  # pragma: no cover - the row cannot vanish under us
            return
        if lease_lost:
            # Another worker owns the job now; touching it would corrupt the lease.
            LOGGER.warning("租约已丢失，不再修改作业 job_id=%s", job_id)
            return
        if error_code is not None:
            forced = None if current.status in TERMINAL_STATUSES | {PAUSED} else FAILED
            self.queue.release(job_id, self.worker_id, status=forced, error_code=error_code)
            return
        forced = COMPLETED if current.status == "running" else None
        self.queue.release(job_id, self.worker_id, status=forced)

    def run_forever(
        self,
        *,
        stop_event: threading.Event | None = None,
        max_jobs: int | None = None,
    ) -> int:
        """Poll until stopped; returns how many jobs this process executed."""

        stop = stop_event or threading.Event()
        processed = 0
        LOGGER.info("worker 启动 worker_id=%s kinds=%s", self.worker_id, ",".join(self.kinds))
        while not stop.is_set():
            job = self.run_once()
            if job is None:
                stop.wait(self.poll_interval)
                continue
            processed += 1
            if max_jobs is not None and processed >= max_jobs:
                break
        LOGGER.info("worker 退出 worker_id=%s processed=%s", self.worker_id, processed)
        return processed

