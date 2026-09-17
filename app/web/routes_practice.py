from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.models import (
    QuestionType,
    ReadingPackage,
    UnitStatus,
)
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service
from .schemas import (
    AnswerResult,
    PracticeResult,
    PracticeSaveResult,
    PracticeSubmission,
)

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")

QUESTION_TYPE_LABELS: dict[QuestionType, str] = {
    QuestionType.MATCHING_HEADINGS: "Matching headings",
    QuestionType.TRUE_FALSE_NOT_GIVEN: "True / False / Not Given",
    QuestionType.YES_NO_NOT_GIVEN: "Yes / No / Not Given",
    QuestionType.MATCHING_INFORMATION: "Matching information",
    QuestionType.MULTIPLE_CHOICE: "Multiple choice",
    QuestionType.SENTENCE_COMPLETION: "Sentence completion",
    QuestionType.SUMMARY_COMPLETION: "Summary completion",
    QuestionType.SHORT_ANSWER: "Short answer",
}
OPTION_PREFIX = re.compile(r"^\s*([A-Za-z]{1,4}|\d{1,2})\s*[.)、]\s*")


@router.get("/practice")
def practice_center(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    items = []
    attempts = service.repository.list_practice_attempts()
    attempts_by_unit: dict[str, list[dict]] = {}
    for attempt in attempts:
        attempts_by_unit.setdefault(attempt["unit_id"], []).append(attempt)
    for corpus in service.repository.list_corpora():
        for unit in service.repository.list_units(corpus.id):
            if unit.status != UnitStatus.COMPLETED:
                continue
            try:
                package = service.load_package(unit.id)
            except (FileNotFoundError, ValueError):
                continue
            unit_attempts = attempts_by_unit.get(unit.id, [])
            submitted = [
                attempt for attempt in unit_attempts if attempt["status"] == "submitted"
            ]
            drafts = [
                attempt for attempt in unit_attempts if attempt["status"] == "in_progress"
            ]
            scored = [attempt for attempt in submitted if attempt["total"]]
            best = (
                max(
                    (attempt["score"] / attempt["total"] for attempt in scored),
                    default=None,
                )
                if scored
                else None
            )
            items.append(
                {
                    "unit": unit,
                    "corpus": corpus,
                    "package": package,
                    "attempts": unit_attempts,
                    "latest": unit_attempts[0] if unit_attempts else None,
                    "latest_submitted": submitted[0] if submitted else None,
                    "latest_draft": drafts[0] if drafts else None,
                    "submitted_count": len(submitted),
                    "best": round(best * 100) if best is not None else None,
                    "state": "empty"
                    if not unit_attempts
                    else ("submitted" if submitted else "in_progress"),
                }
            )
    submitted_attempts = [
        attempt for attempt in attempts if attempt["status"] == "submitted"
    ]
    scoreable = [
        attempt for attempt in submitted_attempts if attempt["total"]
    ]
    average = (
        round(
            sum(attempt["score"] / attempt["total"] for attempt in scoreable)
            / len(scoreable)
            * 100
        )
        if scoreable
        else None
    )
    summary = {
        "available": len(items),
        "in_progress": sum(item["state"] == "in_progress" for item in items),
        "submitted": len(submitted_attempts),
        "average": average,
    }
    return TEMPLATES.TemplateResponse(
        request,
        "practice/index.html",
        {"items": items, "summary": summary},
    )


@router.get("/practice/{unit_id}")
def practice_session(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    fresh: int = 0,
):
    package = _completed_package(service, unit_id)
    attempts = service.repository.list_practice_attempts(unit_id)
    draft = next(
        (attempt for attempt in attempts if attempt["status"] == "in_progress"), None
    )
    submitted = next(
        (attempt for attempt in attempts if attempt["status"] == "submitted"), None
    )
    saved_answers: dict[str, Any] = {}
    attempt_id = ""
    resume_seconds = 0
    if draft is not None and not fresh:
        attempt_id = draft["id"]
        payload = draft["payload"]
        saved_answers = payload.get("answers") or {}
        if isinstance(payload.get("elapsed_seconds"), int):
            resume_seconds = max(0, payload["elapsed_seconds"])
    return TEMPLATES.TemplateResponse(
        request,
        "practice/session.html",
        {
            "package": package,
            "attempt_id": attempt_id,
            "saved_answers": saved_answers,
            "resume_seconds": resume_seconds,
            "paragraph_labels": [
                paragraph.label for paragraph in package.passage.paragraphs
            ],
            "latest_submitted": submitted,
            "question_type_labels": QUESTION_TYPE_LABELS,
        },
    )


@router.post("/practice/{unit_id}/save", response_model=PracticeSaveResult)
def save_practice(
    unit_id: str,
    submission: PracticeSubmission,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    _completed_package(service, unit_id)
    attempt_id = submission.attempt_id or uuid4().hex
    service.repository.save_practice_attempt(
        attempt_id,
        unit_id,
        status="in_progress",
        payload={
            "answers": submission.answers,
            "elapsed_seconds": submission.elapsed_seconds,
        },
    )
    return PracticeSaveResult(attempt_id=attempt_id)


@router.post("/practice/{unit_id}/submit")
async def submit_practice(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Score one attempt and store it.

    A JSON body (used by the page script) receives the full result payload, while
    a form body (the plain HTML form, used when JavaScript is unavailable)
    redirects to the server-rendered result page. Both paths persist the attempt,
    so submitting an answer can never be silently lost.
    """
    package = _completed_package(service, unit_id)
    submission, is_form = await _read_submission(request)
    results, correct_count = _score_submission(package, submission.answers)
    attempt_id = submission.attempt_id or uuid4().hex
    service.repository.save_practice_attempt(
        attempt_id,
        unit_id,
        status="submitted",
        score=correct_count,
        total=len(results),
        payload={
            "answers": submission.answers,
            "elapsed_seconds": submission.elapsed_seconds,
        },
    )
    if is_form:
        return RedirectResponse(
            f"/practice/{unit_id}/result/{attempt_id}", status_code=303
        )
    return PracticeResult(
        attempt_id=attempt_id,
        correct=correct_count,
        total=len(results),
        elapsed_seconds=submission.elapsed_seconds,
        redirect_url=f"/practice/{unit_id}/result/{attempt_id}",
        results=results,
    )


@router.get("/practice/{unit_id}/result/{attempt_id}")
def practice_result(
    request: Request,
    unit_id: str,
    attempt_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    package = _completed_package(service, unit_id)
    attempt = service.repository.get_practice_attempt(attempt_id)
    if attempt is None or attempt["unit_id"] != unit_id:
        raise HTTPException(status_code=404, detail="Attempt not found")
    payload = attempt["payload"]
    answers = payload.get("answers") or {}
    results, correct_count = _score_submission(package, answers)
    total = len(results)
    seconds = payload.get("elapsed_seconds")
    elapsed = seconds if isinstance(seconds, int) and seconds > 0 else 0
    return TEMPLATES.TemplateResponse(
        request,
        "practice/result.html",
        {
            "package": package,
            "attempt": attempt,
            "attempt_id": attempt_id,
            "correct": correct_count,
            "total": total,
            "accuracy": round(correct_count / total * 100) if total else 0,
            "minutes": elapsed // 60,
            "seconds": elapsed % 60,
            "review": _review_groups(package, results),
            "question_type_labels": QUESTION_TYPE_LABELS,
        },
    )


@router.get("/practice/{unit_id}/analysis")
def practice_analysis(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    attempt: str | None = None,
):
    package = _completed_package(service, unit_id)
    evidence_labels = {
        question.evidence_paragraph
        for group in package.question_groups
        for question in group.questions
    }
    answers: dict[str, Any] | None = None
    if attempt:
        saved = service.repository.get_practice_attempt(attempt)
        if saved is not None and saved["unit_id"] == unit_id:
            answers = saved["payload"].get("answers") or {}
    results, _ = _score_submission(package, answers or {})
    return TEMPLATES.TemplateResponse(
        request,
        "practice/analysis.html",
        {
            "package": package,
            "evidence_labels": evidence_labels,
            "review": _review_groups(package, results, graded=answers is not None),
            "graded": answers is not None,
            "attempt_id": attempt if answers is not None else None,
            "question_type_labels": QUESTION_TYPE_LABELS,
        },
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
        raise HTTPException(
            status_code=409, detail="Only completed units can be exported"
        )
    try:
        packages = [service.load_package(unit_id) for unit_id in unit_ids]
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if any(not package.quality_report.passed for package in packages):
        raise HTTPException(
            status_code=409, detail="Only validated packages can be exported"
        )
    destination = service.config.output_dir / "exports" / f"manual-{uuid4().hex}"
    if format_name == "docx" and len(packages) > 1:
        paths = service.exporter.export_workbooks(packages, destination, workbook_size)
    elif format_name in {"json", "html", "docx"}:
        paths = [
            path
            for package in packages
            for path in service.exporter.export_package(
                package, destination, {format_name}
            )
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


def _leading_token(option: str) -> str | None:
    match = OPTION_PREFIX.match(option)
    return match.group(1).strip() if match else None


def _option_variants(options: list[str], answer: str) -> list[str]:
    """Accept either the option text or its leading label ("viii", "B", ...).

    Matching Headings answers are stored as the heading label while Multiple
    Choice answers are stored as the full option text, so both spellings must
    grade as correct whichever one the answering widget submits.
    """
    accepted = [answer]
    normalized_answer = _normalize(answer)
    for option in options:
        token = _leading_token(option)
        if normalized_answer == _normalize(option) or (
            token is not None and normalized_answer == _normalize(token)
        ):
            accepted.append(option)
            if token:
                accepted.append(token)
    return [value for value in dict.fromkeys(accepted) if value]


async def _read_submission(request: Request) -> tuple[PracticeSubmission, bool]:
    """Return the submitted answers and whether the body was an HTML form."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid JSON body") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="JSON body must be an object")
        return PracticeSubmission.model_validate(body), False
    form = await request.form()
    answers: dict[str, str] = {}
    for key, value in form.multi_items():
        if len(key) > 1 and key.startswith("q") and key[1:].isdigit():
            text = value if isinstance(value, str) else ""
            if text.strip():
                answers[key[1:]] = text
    elapsed_raw = str(form.get("elapsed_seconds") or "").strip()
    attempt_id = str(form.get("attempt_id") or "").strip()
    return (
        PracticeSubmission(
            attempt_id=attempt_id[:100] or None,
            answers=answers,
            elapsed_seconds=int(elapsed_raw) if elapsed_raw.isdigit() else None,
        ),
        True,
    )


def _score_submission(
    package: ReadingPackage,
    answers: dict[str, str | list[str]],
) -> tuple[list[AnswerResult], int]:
    results: list[AnswerResult] = []
    correct_count = 0
    for group in package.question_groups:
        for question in group.questions:
            submitted = answers.get(str(question.number), "")
            accepted = [
                *_option_variants(group.options, question.answer),
                *question.acceptable_answers,
            ]
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
    return results, correct_count


def _review_groups(
    package: ReadingPackage,
    results: list[AnswerResult],
    *,
    graded: bool = True,
) -> list[dict[str, Any]]:
    """Group scored answers for the shared review partial."""
    by_number = {result.number: result for result in results}
    groups: list[dict[str, Any]] = []
    for group in package.question_groups:
        questions: list[dict[str, Any]] = []
        for question in group.questions:
            result = by_number.get(question.number)
            questions.append(
                {
                    "number": question.number,
                    "prompt": question.prompt,
                    "answer": question.answer,
                    "acceptable_answers": question.acceptable_answers,
                    "evidence_paragraph": question.evidence_paragraph,
                    "evidence_quote": question.evidence_quote,
                    "chinese_explanation": question.chinese_explanation,
                    "distractor_explanations": question.distractor_explanations,
                    "submitted": result.submitted if result else "",
                    "correct": (result.correct if result else False) if graded else None,
                }
            )
        groups.append(
            {
                "type": group.type.value,
                "label": QUESTION_TYPE_LABELS.get(group.type, group.type.value),
                "instructions": group.instructions,
                "word_limit": group.word_limit,
                "options": list(group.options),
                "questions": questions,
                "correct": (
                    sum(1 for question in questions if question["correct"])
                    if graded
                    else None
                ),
            }
        )
    return groups


def _matches(
    submitted: str | list[str],
    accepted: list[str],
    question_type: QuestionType,
) -> bool:
    if question_type == QuestionType.MULTIPLE_CHOICE and isinstance(submitted, list):
        submitted_set = {_normalize(value) for value in submitted}
        return any(
            submitted_set
            == {
                _normalize(part)
                for part in answer.replace(";", ",").split(",")
                if part.strip()
            }
            for answer in accepted
        )
    if isinstance(submitted, list):
        return False
    normalized = _normalize(submitted)
    return bool(normalized) and normalized in {_normalize(answer) for answer in accepted}
