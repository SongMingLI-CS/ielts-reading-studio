from __future__ import annotations

import json
from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.cli import parse_range
from app.models import Difficulty, QuestionType, UnitStatus
from app.pipeline.queue import QueueLimitError
from app.pipeline.service import ReadingStudioService
from app.pipeline.tasks import queue_sample, queue_status_for, sample_status_path
from app.planning.units import DEFAULT_QUESTION_TYPES, default_question_types
from app.security.budget import BudgetError

from .dependencies import get_service
from .glossary import (
    ACTIVE_JOB_STATUSES,
    difficulty_rows,
    job_kind_label,
    job_status_class,
    job_status_label,
    type_rows,
)
from .templating import templates

router = APIRouter()
TEMPLATES = templates()
RANGE_EXAMPLES = "1-20 · 1,3,8-12 · all"


def _ordinal_span(ordinals: list[int]) -> str:
    """把章节号写成人话：连续区间给区间，零散列表给个数。"""

    if not ordinals:
        return ""
    ordered = sorted(ordinals)
    if len(ordered) == 1:
        return f"第 {ordered[0]} 章"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"第 {ordered[0]}–{ordered[-1]} 章"
    return f"{len(ordered)} 个章节"


def _sample_status(service: ReadingStudioService, corpus_id: str) -> dict[str, Any] | None:
    path = sample_status_path(service, corpus_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _batch_idempotency_key(
    corpus_id: str,
    ordinals: list[int] | None,
    difficulty: Difficulty,
    question_types: list[str],
    batch_size: int,
    concurrency: int,
) -> str:
    """One key per identical batch request, so a double-click cannot buy two runs."""

    fingerprint = json.dumps(
        {
            "ordinals": sorted(ordinals) if ordinals else "all",
            "difficulty": difficulty.value,
            "question_types": sorted(question_types),
            "batch_size": batch_size,
            "concurrency": concurrency,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"batch:{corpus_id}:{sha256(fingerprint.encode('utf-8')).hexdigest()[:32]}"


def _completed_unit_row(service: ReadingStudioService, unit: Any) -> dict[str, Any]:
    """Readable facts about a finished unit so a sample can be judged in the browser."""
    row: dict[str, Any] = {
        "id": unit.id,
        "ordinal": unit.ordinal,
        "difficulty": unit.difficulty.value,
        "question_types": [
            value.value.replace("_", " ") for value in unit.question_types
        ],
        "title": None,
        "word_count": None,
        "question_count": None,
        "passed": None,
    }
    try:
        package = service.load_package(unit.id)
    except (FileNotFoundError, ValueError):
        return row
    row.update(
        {
            "title": package.passage.title,
            "word_count": package.passage.word_count,
            "question_count": sum(len(group.questions) for group in package.question_groups),
            "passed": package.quality_report.passed,
        }
    )
    return row


@router.get("/corpora/{corpus_id}/configure")
def configure_job(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    sample: str | None = None,
):
    try:
        estimate = service.estimate_corpus(corpus_id)
        details = service.inspect_corpus(corpus_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    units = service.repository.list_units(corpus_id)
    first_unit = units[0] if units else None
    completed_units = [
        _completed_unit_row(service, unit)
        for unit in units
        if unit.status == UnitStatus.COMPLETED
    ]
    recommended = {
        difficulty.value: [value.value for value in values]
        for difficulty, values in DEFAULT_QUESTION_TYPES.items()
    }
    return TEMPLATES.TemplateResponse(
        request,
        "jobs/configure.html",
        {
            "details": details,
            "estimate": estimate,
            "question_types": list(QuestionType),
            "type_rows": type_rows(recommended),
            "difficulty_rows": difficulty_rows(),
            "recommended_types": recommended,
            "completed_units": completed_units,
            "completed_count": len(completed_units),
            "unit_count": len(units),
            "sample_status": _sample_status(service, corpus_id),
            "sample_flag": sample,
            "has_api_key": service.config.deepseek_api_key is not None,
            "range_examples": RANGE_EXAMPLES,
            "selected_difficulty": first_unit.difficulty.value if first_unit else "standard",
            "selected_question_types": (
                {value.value for value in first_unit.question_types} if first_unit else set()
            ),
        },
    )


@router.post("/corpora/{corpus_id}/sample")
def start_sample(
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    difficulty: Annotated[Difficulty, Form()] = Difficulty.STANDARD,
    question_types: Annotated[list[str] | None, Form()] = None,
):
    """Queue exactly one sample of the chosen shape; approval stays manual."""
    if service.repository.get_corpus(corpus_id) is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    if service.config.deepseek_api_key is None:
        return RedirectResponse(
            f"/corpora/{corpus_id}/configure?sample=no-key", status_code=303
        )
    if not service.repository.list_units(corpus_id):
        return RedirectResponse(
            f"/corpora/{corpus_id}/configure?sample=no-units", status_code=303
        )
    selected = [QuestionType(value) for value in (question_types or [])]
    if not selected:
        selected = default_question_types(difficulty)
    if len(selected) != 3 or len(set(selected)) != 3:
        return RedirectResponse(
            f"/corpora/{corpus_id}/configure?sample=bad-types", status_code=303
        )
    try:
        queue_sample(service, corpus_id, difficulty, selected)
    except QueueLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return RedirectResponse(
        f"/corpora/{corpus_id}/configure?sample=started", status_code=303
    )


@router.post("/corpora/{corpus_id}/approve-sample")
def approve_sample(
    corpus_id: str,
    unit_id: Annotated[str, Form()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        service.approve_sample(corpus_id, unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/corpora/{corpus_id}/configure", status_code=303)


@router.post("/corpora/{corpus_id}/jobs")
def start_job(
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    difficulty: Annotated[Difficulty, Form()] = Difficulty.STANDARD,
    range_spec: Annotated[str, Form()] = "all",
    question_types: Annotated[list[str] | None, Form()] = None,
    batch_size: Annotated[int, Form(ge=1, le=100)] = 20,
    concurrency: Annotated[int, Form(ge=1, le=8)] = 2,
):
    """Queue a batch. The worker runs it; this request never starts a model call."""
    try:
        if not service.is_corpus_approved(corpus_id):
            raise PermissionError("A completed sample must be explicitly approved before batch generation")
        ordinals = parse_range(range_spec)
        selected_types = [QuestionType(value) for value in (question_types or [])]
        if len(selected_types) != 3 or len(set(selected_types)) != 3:
            raise ValueError("Exactly three distinct question types are required")
        job = service.create_job(
            corpus_id,
            ordinals,
            batch_size=batch_size,
            concurrency=concurrency,
            difficulty=difficulty,
            question_types=selected_types,
            idempotency_key=_batch_idempotency_key(
                corpus_id,
                ordinals,
                difficulty,
                [value.value for value in selected_types],
                batch_size,
                concurrency,
            ),
        )
    except QueueLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except BudgetError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/jobs/{job['id']}", status_code=303)


@router.get("/jobs")
def job_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    status: str | None = None,
    corpus: str | None = None,
):
    """全部生成任务集中一处。

    任务页此前只能从"提交后跳转"那一次进入：离开页面后就再也找不到进行中或失败
    的任务，重试/继续/取消全部失去入口。这个索引就是那个缺失的入口。
    """

    selected = status if status in {"active", "failed", "finished"} else None
    corpus_id = corpus or None
    rows = [_job_row(service, job) for job in service.repository.list_jobs(corpus_id)]
    if selected == "active":
        visible = [row for row in rows if row["status"] in ACTIVE_JOB_STATUSES]
    elif selected == "failed":
        visible = [row for row in rows if row["has_failures"]]
    elif selected == "finished":
        visible = [
            row
            for row in rows
            if row["status"] not in ACTIVE_JOB_STATUSES and not row["has_failures"]
        ]
    else:
        visible = rows
    return TEMPLATES.TemplateResponse(
        request,
        "jobs/index.html",
        {
            "rows": visible,
            "total": len(rows),
            "selected_status": selected,
            "corpus_filter": corpus_id,
            "corpus_name": rows[0]["corpus_name"] if corpus_id and rows else None,
            "status_tabs": [
                (_jobs_href(None, corpus_id), "全部", None),
                (_jobs_href("active", corpus_id), "进行中 / 已暂停", "active"),
                (_jobs_href("failed", corpus_id), "有失败单元", "failed"),
                (_jobs_href("finished", corpus_id), "已完成", "finished"),
            ],
        },
    )


def _jobs_href(status: str | None, corpus: str | None) -> str:
    params = [
        f"{key}={value}"
        for key, value in (("status", status), ("corpus", corpus))
        if value
    ]
    return f"/jobs?{'&'.join(params)}" if params else "/jobs"


def _job_row(service: ReadingStudioService, job: dict[str, Any]) -> dict[str, Any]:
    """一个任务的可读摘要：材料、章节范围、进度、失败原因入口。"""

    units = service.repository.list_job_units(job["id"])
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.status.value] = counts.get(unit.status.value, 0) + 1
    payload = job["payload"] or {}
    ordinals = [int(value) for value in payload.get("ordinals") or []]
    corpus = service.repository.get_corpus(job["corpus_id"])
    total = len(units)
    done = counts.get(UnitStatus.COMPLETED.value, 0)
    failed = counts.get(UnitStatus.FAILED.value, 0)
    needs_review = counts.get(UnitStatus.NEEDS_REVIEW.value, 0)
    # 单元失败或待人工确认都算"这个任务有事要处理"：批次跑完时这两类会把任务
    # 标成 completed_with_errors，筛选与"查看并重试"按钮都要看得见它们。
    has_failures = bool(failed or needs_review or job["status"] == "completed_with_errors")
    span = _ordinal_span(ordinals)
    kind_label = job_kind_label(job.get("kind") or "")
    return {
        "id": job["id"],
        "status": job["status"],
        "status_label": job_status_label(job["status"]),
        "status_class": job_status_class(job["status"]),
        "kind": job.get("kind") or "",
        "kind_label": kind_label,
        "corpus_id": job["corpus_id"],
        "corpus_name": corpus.name if corpus else job["corpus_id"][:8],
        "title": f"{span}{kind_label}任务" if span else f"{kind_label}任务",
        "total": total,
        "done": done,
        "failed": failed,
        "needs_review": needs_review,
        "has_failures": has_failures,
        "progress": round(done / total * 100) if total else 0,
        "difficulty": payload.get("difficulty"),
        "question_types": payload.get("question_types") or [],
        "attempts": job.get("attempts") or 0,
        "error_code": job.get("error_code"),
        "updated_at": job.get("updated_at"),
        "created_at": job.get("created_at"),
        "updated_label": _moment_label(job.get("updated_at")),
        "created_label": _moment_label(job.get("created_at")),
    }


def _moment_label(value: Any) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else "—"


@router.get("/jobs/{job_id}")
def job_detail(
    request: Request,
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return TEMPLATES.TemplateResponse(
        request,
        "jobs/detail.html",
        {
            "job": job,
            "units": service.repository.list_job_units(job_id),
            "queue_state": queue_status_for(service, job_id),
        },
    )


@router.get("/jobs/{job_id}/units")
def job_units(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    after: Annotated[int, Query(ge=0)] = 0,
):
    del after
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    units = service.repository.list_job_units(job_id)
    return {
        "job_status": job["status"],
        "units": [
            {"id": unit.id, "ordinal": unit.ordinal, "status": unit.status.value}
            for unit in units
        ],
    }


@router.post("/jobs/{job_id}/pause")
def pause_job(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Stop claiming new units; the in-flight stage still saves its result."""
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not service.queue.request_pause(job_id):
        raise HTTPException(status_code=409, detail="当前状态不能暂停")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Requeue a paused job and release any unit a dead worker left running."""
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not service.queue.resume(job_id):
        raise HTTPException(status_code=409, detail="当前状态不能继续")
    service.repository.recover_interrupted_units()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Create a fresh job for the failed units; nothing runs inside this request."""
    try:
        job = service.retry_job(job_id, failed_only=True)
    except QueueLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except BudgetError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/jobs/{job['id']}", status_code=303)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Terminal state; the runner stops at the next unit boundary."""
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not service.queue.cancel(job_id):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
