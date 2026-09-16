from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.cli import parse_range
from app.models import Difficulty, QuestionType, UnitStatus
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")


@router.get("/corpora/{corpus_id}/configure")
def configure_job(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        estimate = service.estimate_corpus(corpus_id)
        details = service.inspect_corpus(corpus_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    units = service.repository.list_units(corpus_id)
    first_unit = units[0] if units else None
    return TEMPLATES.TemplateResponse(
        request,
        "jobs/configure.html",
        {
            "details": details,
            "estimate": estimate,
            "question_types": list(QuestionType),
            "completed_units": [
                unit
                for unit in units
                if unit.status == UnitStatus.COMPLETED
            ],
            "selected_difficulty": first_unit.difficulty.value if first_unit else "standard",
            "selected_question_types": {
                value.value for value in first_unit.question_types
            } if first_unit else set(),
        },
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
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    difficulty: Annotated[Difficulty, Form()] = Difficulty.STANDARD,
    range_spec: Annotated[str, Form()] = "all",
    question_types: Annotated[list[str] | None, Form()] = None,
    batch_size: Annotated[int, Form(ge=1, le=100)] = 20,
    concurrency: Annotated[int, Form(ge=1, le=8)] = 2,
):
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
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background.add_task(service.run_job, job["id"])
    return RedirectResponse(f"/jobs/{job['id']}", status_code=303)


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
        {"job": job, "units": service.repository.list_job_units(job_id)},
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
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    service.repository.update_job(job_id, status="paused", payload=job["payload"])
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    background.add_task(service.resume_job, job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        job = service.retry_job(job_id, failed_only=True)
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(service.run_job, job["id"])
    return RedirectResponse(f"/jobs/{job['id']}", status_code=303)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    job = service.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    service.repository.update_job(job_id, status="cancelled", payload=job["payload"])
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
