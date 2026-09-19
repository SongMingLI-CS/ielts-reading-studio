from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.pipeline.service import ReadingStudioService

from .dependencies import get_service
from .routes_practice import QUESTION_TYPE_LABELS
from .templating import templates

router = APIRouter()
TEMPLATES = templates()


def _latest_job_id(service: ReadingStudioService) -> str | None:
    jobs = service.repository.list_jobs()
    if not jobs:
        return None
    ordered = sorted(jobs, key=lambda job: str(job.get("created_at") or ""), reverse=True)
    return str(ordered[0]["id"])


@router.get("/review")
def review_queue(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    sampled: int | None = None,
    decided: str | None = None,
):
    queue = service.review_queue()
    duplicates = service.duplicate_report()
    return TEMPLATES.TemplateResponse(
        request,
        "review/index.html",
        {
            **queue,
            "duplicates": duplicates[:5],
            "duplicate_count": len(duplicates),
            "sampled": sampled,
            "decided": decided,
            "review_sample_rate": service.config.review_sample_rate,
            "review_sample_min": service.config.review_sample_min,
            "question_type_labels": QUESTION_TYPE_LABELS,
        },
    )


@router.post("/review/sample")
def sample_more(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    job_id: Annotated[str | None, Form()] = None,
    size: Annotated[int | None, Form()] = None,
):
    target = job_id or _latest_job_id(service)
    if not target:
        raise HTTPException(status_code=409, detail="No job to sample from")
    if service.repository.get_job(target) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = service.sample_job_units(target, size=size, force=True)
    if not rows:
        raise HTTPException(
            status_code=409,
            detail="This job has no completed unit to sample yet",
        )
    return RedirectResponse(f"/review?sampled={len(rows)}", status_code=303)


@router.post("/review/{unit_id}/decide")
def decide_sample(
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    decision: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
):
    if decision not in {"pass", "rework"}:
        raise HTTPException(status_code=422, detail="Unsupported decision")
    try:
        result = service.decide_sample(
            unit_id, approved=decision == "pass", note=note.strip()[:500]
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(f"/review?decided={result['status']}", status_code=303)


@router.get("/review/similarity")
def similarity_report(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    threshold: float | None = None,
):
    if threshold is not None and not 0.2 <= threshold <= 1.0:
        raise HTTPException(status_code=422, detail="Threshold must be between 0.2 and 1")
    pairs = service.duplicate_report(threshold=threshold)
    checked = len(service.build_question_index().stems)
    return TEMPLATES.TemplateResponse(
        request,
        "review/similarity.html",
        {
            "pairs": pairs,
            "questions_checked": checked,
            "threshold": threshold
            if threshold is not None
            else service.config.question_report_threshold,
            "blocking_threshold": service.config.question_duplicate_threshold,
        },
    )
