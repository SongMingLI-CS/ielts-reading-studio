from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.models import QuestionType, UnitStatus
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service
from .schemas import AnswerResult, PracticeResult, PracticeSubmission

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")


@router.get("/practice/{unit_id}")
def practice_session(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    package = _completed_package(service, unit_id)
    return TEMPLATES.TemplateResponse(
        request,
        "practice/session.html",
        {"package": package},
    )


@router.post("/practice/{unit_id}/submit", response_model=PracticeResult)
def submit_practice(
    unit_id: str,
    submission: PracticeSubmission,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    package = _completed_package(service, unit_id)
    results: list[AnswerResult] = []
    correct_count = 0
    for group in package.question_groups:
        for question in group.questions:
            submitted = submission.answers.get(str(question.number), "")
            accepted = [question.answer, *question.acceptable_answers]
            correct = _matches(submitted, accepted, group.type)
            correct_count += int(correct)
            results.append(
                AnswerResult(
                    number=question.number,
                    correct=correct,
                    submitted=submitted,
                    answer=question.answer,
                    acceptable_answers=question.acceptable_answers,
                    evidence_paragraph=question.evidence_paragraph,
                    evidence_quote=question.evidence_quote,
                    chinese_explanation=question.chinese_explanation,
                    distractor_explanations=question.distractor_explanations,
                )
            )
    return PracticeResult(
        correct=correct_count,
        total=len(results),
        elapsed_seconds=submission.elapsed_seconds,
        results=results,
    )


@router.get("/practice/{unit_id}/analysis")
def practice_analysis(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    return TEMPLATES.TemplateResponse(
        request,
        "practice/analysis.html",
        {"package": _completed_package(service, unit_id)},
    )


@router.get("/exports")
def export_center(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    created: str | None = None,
):
    completed = [
        unit
        for corpus in service.repository.list_corpora()
        for unit in service.repository.list_units(corpus.id)
        if unit.status == UnitStatus.COMPLETED
    ]
    return TEMPLATES.TemplateResponse(
        request,
        "exports/index.html",
        {"units": completed, "created": created},
    )


@router.post("/exports")
def create_exports(
    unit_ids: Annotated[list[str], Form()],
    format_name: Annotated[str, Form(alias="format")],
    service: Annotated[ReadingStudioService, Depends(get_service)],
    workbook_size: Annotated[int, Form(ge=20, le=50)] = 20,
):
    units = [service.repository.get_unit(unit_id) for unit_id in unit_ids]
    if any(unit is None or unit.status != UnitStatus.COMPLETED for unit in units):
        raise HTTPException(status_code=409, detail="Only completed units can be exported")
    try:
        packages = [service.load_package(unit_id) for unit_id in unit_ids]
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if any(not package.quality_report.passed for package in packages):
        raise HTTPException(status_code=409, detail="Only validated packages can be exported")
    destination = service.config.output_dir / "exports" / f"manual-{uuid4().hex}"
    if format_name == "docx" and len(packages) > 1:
        paths = service.exporter.export_workbooks(packages, destination, workbook_size)
    elif format_name in {"json", "html", "docx"}:
        paths = [
            path
            for package in packages
            for path in service.exporter.export_package(package, destination, {format_name})
        ]
    else:
        raise HTTPException(status_code=422, detail="Unsupported export format")
    return RedirectResponse(f"/exports?created={len(paths)}", status_code=303)


def _completed_package(service: ReadingStudioService, unit_id: str):
    unit = service.repository.get_unit(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if unit.status != UnitStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Unit is not completed")
    try:
        package = service.load_package(unit_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not package.quality_report.passed:
        raise HTTPException(status_code=409, detail="Package did not pass validation")
    return package


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    return " ".join(text.strip().split()).casefold()


def _matches(
    submitted: str | list[str],
    accepted: list[str],
    question_type: QuestionType,
) -> bool:
    if question_type == QuestionType.MULTIPLE_CHOICE and isinstance(submitted, list):
        submitted_set = {_normalize(value) for value in submitted}
        return any(
            submitted_set
            == {_normalize(part) for part in answer.replace(";", ",").split(",") if part.strip()}
            for answer in accepted
        )
    if isinstance(submitted, list):
        return False
    normalized = _normalize(submitted)
    return normalized in {_normalize(answer) for answer in accepted}
