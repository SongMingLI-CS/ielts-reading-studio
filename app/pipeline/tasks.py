"""Durable task helpers shared by the web routes and the worker.

The web process never generates anything itself: it records the request as a queued job
and the worker executes it. These helpers keep the payload shape and the status file
format in one place so both sides agree.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import redact_secrets
from app.models import Difficulty, QuestionType
from app.pipeline.queue import SAMPLE_KIND, JobQueue, QueueJob

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.pipeline.service import ReadingStudioService


def sample_status_path(service: ReadingStudioService, corpus_id: str) -> Path:
    """Where the configure page reads the latest sample outcome from."""

    return service.store.root / "reports" / f"sample-{corpus_id[:8]}.json"


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sample_idempotency_key(
    corpus_id: str, difficulty: Difficulty, question_types: list[QuestionType]
) -> str:
    """Stable key for one sample request shape, so double-clicks reuse the job."""

    types = ",".join(sorted(value.value for value in question_types))
    return f"sample:{corpus_id}:{difficulty.value}:{types}"


def queue_sample(
    service: ReadingStudioService,
    corpus_id: str,
    difficulty: Difficulty,
    question_types: list[QuestionType],
) -> QueueJob:
    """Record a sample request as a queued job; nothing is generated here."""

    record = {
        "difficulty": difficulty.value,
        "question_types": [value.value for value in question_types],
    }
    job = service.queue.enqueue(
        corpus_id=corpus_id,
        payload={
            "corpus_id": corpus_id,
            "difficulty": difficulty.value,
            "question_types": record["question_types"],
            "status_path": str(sample_status_path(service, corpus_id)),
        },
        kind=SAMPLE_KIND,
        idempotency_key=sample_idempotency_key(corpus_id, difficulty, question_types),
    )
    _write_status(sample_status_path(service, corpus_id), {**record, "status": "queued"})
    return job


def run_sample_task(service: ReadingStudioService, payload: dict[str, Any]) -> None:
    """Worker side of :func:`queue_sample`; records the outcome for the page."""

    corpus_id = str(payload["corpus_id"])
    record = {
        "difficulty": payload["difficulty"],
        "question_types": list(payload["question_types"]),
    }
    status_path = Path(payload.get("status_path") or sample_status_path(service, corpus_id))
    _write_status(
        status_path,
        {**record, "status": "running", "started_at": dt.datetime.now(dt.UTC).isoformat()},
    )
    try:
        service.generate_sample(
            corpus_id,
            Difficulty(payload["difficulty"]),
            [QuestionType(value) for value in payload["question_types"]],
        )
    except Exception as exc:
        _write_status(
            status_path,
            {
                **record,
                "status": "failed",
                "finished_at": dt.datetime.now(dt.UTC).isoformat(),
                "error": redact_secrets(str(exc)),
            },
        )
        raise
    _write_status(
        status_path,
        {**record, "status": "completed", "finished_at": dt.datetime.now(dt.UTC).isoformat()},
    )


def queue_status_for(service: ReadingStudioService, job_id: str) -> dict[str, Any]:
    """Queue state for the job page: waiting / running / interrupted / offline worker."""

    queue: JobQueue = service.queue
    job = queue.get(job_id)
    if job is None:
        return {}
    return {
        "label": job.waiting_reason(),
        "status": job.status,
        "kind": job.kind,
        "attempts": job.attempts,
        "worker_id": job.worker_id,
        "error_code": job.error_code,
        "lease_expired": job.lease_expired,
        "queued": queue.counts().get("queued", 0),
    }
