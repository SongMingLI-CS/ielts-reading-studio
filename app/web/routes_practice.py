from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from app.models import (
    QuestionType,
    ReadingPackage,
    UnitStatus,
)
from app.pipeline.service import ReadingStudioService
from app.vocabulary import due_rows, study_rows, tracked_rows

from .dependencies import get_service
from .progress import draft_progress
from .routes_vocabulary import review_session_context
from .schemas import (
    AnswerResult,
    PracticeResult,
    PracticeSaveResult,
    PracticeSubmission,
)
from .templating import templates

router = APIRouter()
TEMPLATES = templates()

#: 复习中心的三个标签：错题 / 生词本 / 词汇复习。顺序即呈现顺序。
REVIEW_TABS: dict[str, str] = {"mistakes": "错题", "words": "生词本", "due": "词汇复习"}
#: 复习中心的筛选链接要带回自己的标签，否则点一下就被甩回旧页面。
REVIEW_BASE = "/practice/review"
#: 每个标签复用哪一份内容：同一份模板，独立页面与复习中心共用。
REVIEW_PARTIALS: dict[str, str] = {
    "mistakes": "practice/review/_mistakes.html",
    "words": "practice/review/_words.html",
    "due": "practice/review/_due.html",
}

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
    q: str | None = None,
    state: str | None = None,
):
    return TEMPLATES.TemplateResponse(
        request, "practice/index.html", practice_library_context(service, q, state)
    )


#: 练习中心的状态筛选：值 → 标签。顺序就是屏幕上的顺序。
PRACTICE_STATES: dict[str, str] = {
    "in_progress": "进行中",
    "new": "未开始",
    "done": "已完成",
}


def practice_library_context(
    service: ReadingStudioService, query: str | None, state: str | None
) -> dict[str, Any]:
    """练习中心的上下文：篇目、草稿进度、状态筛选与搜索。

    搜索与筛选都在服务端做，链接可以直接分享（?q=&state=），也不依赖脚本。
    """

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
            latest_draft = drafts[0] if drafts else None
            progress = (
                draft_progress(package, latest_draft["payload"]) if latest_draft else None
            )
            # 只要还有草稿就算"进行中"，即使这一篇以前提交过：草稿比旧成绩更应该被接着写，
            # 显示成"重做"会带上 ?fresh=1 并把已经写好的答案清掉。
            if latest_draft is not None or (unit_attempts and not submitted):
                item_state = "in_progress"
            elif submitted:
                item_state = "submitted"
            else:
                item_state = "empty"
            items.append(
                {
                    "unit": unit,
                    "corpus": corpus,
                    "package": package,
                    "attempts": unit_attempts,
                    "latest_submitted": submitted[0] if submitted else None,
                    "latest_draft": latest_draft,
                    "submitted_count": len(submitted),
                    "best": round(best * 100) if best is not None else None,
                    "draft": progress,
                    "state": item_state,
                }
            )

    needle = (query or "").strip().casefold()
    selected = state if state in PRACTICE_STATES else None
    visible = [
        item
        for item in items
        if (not needle or needle in item["package"].passage.title.casefold())
        and (
            selected is None
            or (selected == "new" and item["state"] == "empty")
            or (selected == "done" and item["state"] == "submitted")
            or (selected == "in_progress" and item["state"] == "in_progress")
        )
    ]

    submitted_attempts = [
        attempt for attempt in attempts if attempt["status"] == "submitted"
    ]
    scoreable = [attempt for attempt in submitted_attempts if attempt["total"]]
    average = (
        round(
            sum(attempt["score"] / attempt["total"] for attempt in scoreable)
            / len(scoreable)
            * 100
        )
        if scoreable
        else None
    )
    counts = {
        "all": len(items),
        "in_progress": sum(item["state"] == "in_progress" for item in items),
        "new": sum(item["state"] == "empty" for item in items),
        "done": sum(item["state"] == "submitted" for item in items),
    }
    return {
        "items": visible,
        "total_items": len(items),
        "matched": len(visible),
        "query": query or "",
        "selected_state": selected,
        "state_tabs": [
            (value, label, counts[value]) for value, label in PRACTICE_STATES.items()
        ],
        "state_counts": counts,
        # 一篇都没做过时给"开始第一篇"，全部做过时给"去复习"的引导
        "all_done": bool(items) and counts["done"] == len(items),
        "summary": {
            "available": len(items),
            "in_progress": counts["in_progress"],
            "submitted": len(submitted_attempts),
            "average": average,
        },
    }


@router.get("/practice/history")
def practice_history(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    rows = _scored_attempts(service)
    by_unit: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_unit.setdefault(
            row["unit"].id,
            {"unit": row["unit"], "package": row["package"], "attempts": [], "best": 0},
        )
        entry["attempts"].append(row)
        entry["best"] = max(entry["best"], row["accuracy"])
    passages = sorted(
        by_unit.values(),
        key=lambda entry: str(entry["attempts"][0]["submitted_at"]),
        reverse=True,
    )
    summary = {
        "attempts": len(rows),
        "passages": len(by_unit),
        "average": round(sum(row["accuracy"] for row in rows) / len(rows)) if rows else None,
        "minutes": sum(row["elapsed"] for row in rows) // 60,
        "questions": sum(row["total"] for row in rows),
        "correct": sum(row["correct"] for row in rows),
    }
    return TEMPLATES.TemplateResponse(
        request,
        "practice/history.html",
        {
            "rows": rows,
            "passages": passages,
            "summary": summary,
            "type_stats": _type_accuracy(rows),
        },
    )


@router.get("/practice/mistakes")
def practice_mistakes(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    type: str | None = None,
):
    return TEMPLATES.TemplateResponse(
        request, "practice/mistakes.html", mistakes_context(service, type)
    )


@router.get("/practice/review")
def practice_review(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    tab: str = "mistakes",
    type: str | None = None,
    scope: str | None = None,
    mode: str = "due",
    feedback: str | None = None,
    answer: str | None = None,
    last: str | None = None,
):
    """复习中心：错题、生词本、词汇复习合成一个入口。

    这三个页面此前是三个平行入口（另有词汇记忆与练习里的生词入口），学习者得先猜
    "我今天想复习的东西叫什么名字"。这里给一张有三个标签的清单，筛选链接与表单
    提交都带上所在标签，不会把人甩回旧页面。
    """

    selected = tab if tab in REVIEW_TABS else "mistakes"
    if selected == "words":
        context = vocabulary_book_context(service, scope)
    elif selected == "due":
        context = review_session_context(
            service, mode=mode, feedback=feedback, answer=answer, last=last
        )
    else:
        context = mistakes_context(service, type)
    counts = review_tab_counts(service)
    return TEMPLATES.TemplateResponse(
        request,
        "practice/review/index.html",
        {
            **context,
            "tab": selected,
            "partial": REVIEW_PARTIALS[selected],
            "tabs": [
                (value, label, counts[value]) for value, label in REVIEW_TABS.items()
            ],
            "review_base": REVIEW_BASE,
        },
    )


def mistakes_context(service: ReadingStudioService, selected: str | None) -> dict[str, Any]:
    """错题本的内容：独立页面与复习中心共用，避免两处筛选规则不一样。"""

    items = mistake_items(service)
    available = sorted({entry["type"].value for entry in items})
    type_ = selected if selected in available else None
    visible = [entry for entry in items if type_ is None or entry["type"].value == type_]
    rows = _scored_attempts(service)
    return {
        "items": visible[:200],
        "total_items": len(items),
        "available_types": [
            {"value": value, "label": QUESTION_TYPE_LABELS[QuestionType(value)]}
            for value in available
        ],
        "selected_type": type_,
        "type_stats": _type_accuracy(rows),
        "attempt_count": len(rows),
    }


def vocabulary_book_context(service: ReadingStudioService, scope: str | None) -> dict[str, Any]:
    """生词本的内容：收藏 / 已掌握 / 未标记三档筛选。"""

    rows = _vocabulary_rows(service)
    saved = [row for row in rows if row["status"] == "saved"]
    known = [row for row in rows if row["status"] == "known"]
    selected = scope if scope in {"saved", "known", "untagged"} else None
    if selected == "saved":
        visible = saved
    elif selected == "known":
        visible = known
    elif selected == "untagged":
        visible = [row for row in rows if not row["status"]]
    else:
        visible = rows
    return {
        "rows": visible,
        "total": len(rows),
        "saved_count": len(saved),
        "known_count": len(known),
        "selected_scope": selected,
        "question_total": 0,
    }


def review_tab_counts(service: ReadingStudioService) -> dict[str, int]:
    """三个标签各自有多少东西要复习；数字来自记录，不是估算。"""

    now = datetime.now(UTC)
    vocabulary = _vocabulary_rows(service)
    tracked = tracked_rows(study_rows(service))
    return {
        "mistakes": len(mistake_items(service)),
        "words": sum(1 for row in vocabulary if row["status"] == "saved"),
        "due": len(due_rows(tracked, now)),
    }


def mistake_items(service: ReadingStudioService) -> list[dict[str, Any]]:
    """每一道做错过的题，按"错得最多"排序；错误次数与最后出现时间都带上。"""
    rows = _scored_attempts(service)
    wrong: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:  # newest first, so the first sighting is the latest attempt
        for result in row["results"]:
            if result.correct:
                continue
            key = (row["unit"].id, result.number)
            entry = wrong.get(key)
            if entry is None:
                wrong[key] = {
                    "unit": row["unit"],
                    "package": row["package"],
                    "result": result,
                    "attempt_id": row["attempt"]["id"],
                    "last_seen": row["submitted_at"],
                    "wrong_count": 1,
                    "attempt_count": 1,
                }
            else:
                entry["wrong_count"] += 1
                entry["attempt_count"] += 1
    for entry in wrong.values():
        questions = [
            question
            for group in entry["package"].question_groups
            for question in group.questions
        ]
        entry["type"] = next(
            group.type
            for group in entry["package"].question_groups
            if any(question.number == entry["result"].number for question in group.questions)
        )
        entry["label"] = QUESTION_TYPE_LABELS.get(entry["type"], entry["type"].value)
        entry["prompt"] = next(
            (
                question.prompt
                for question in questions
                if question.number == entry["result"].number
            ),
            "",
        )
        entry["question"] = next(
            (
                question
                for question in questions
                if question.number == entry["result"].number
            ),
            None,
        )
    items = sorted(
        wrong.values(),
        key=lambda entry: str(entry["last_seen"]),
        reverse=True,
    )
    items.sort(key=lambda entry: -entry["wrong_count"])
    return items


def _vocabulary_rows(
    service: ReadingStudioService,
) -> list[dict[str, Any]]:
    """All vocabulary from completed packages, merged by word across passages."""
    marks = service.repository.list_vocabulary_marks()
    merged: dict[str, dict[str, Any]] = {}
    for corpus in service.repository.list_corpora():
        for unit in service.repository.list_units(corpus.id):
            if unit.status != UnitStatus.COMPLETED:
                continue
            try:
                package = service.load_package(unit.id)
            except (FileNotFoundError, ValueError):
                continue
            for entry in package.passage.vocabulary:
                key = entry.word.strip().casefold()
                if not key:
                    continue
                row = merged.setdefault(
                    key,
                    {
                        "word": entry.word.strip(),
                        "pronunciation": entry.pronunciation,
                        "part_of_speech": entry.part_of_speech,
                        "chinese_meaning": entry.chinese_meaning,
                        "collocations": list(entry.collocations),
                        "example": entry.example,
                        "passages": [],
                        "status": marks.get(key, ""),
                    },
                )
                if package.passage.title not in row["passages"]:
                    row["passages"].append(package.passage.title)
                row["pronunciation"] = row["pronunciation"] or entry.pronunciation
                row["part_of_speech"] = row["part_of_speech"] or entry.part_of_speech
                row["chinese_meaning"] = row["chinese_meaning"] or entry.chinese_meaning
                row["example"] = row["example"] or entry.example
                for collocation in entry.collocations:
                    if collocation not in row["collocations"]:
                        row["collocations"].append(collocation)
    rows = sorted(merged.values(), key=lambda row: (-len(row["passages"]), row["word"]))
    for row in rows:
        row["passage_count"] = len(row["passages"])
    return rows


@router.get("/practice/vocabulary")
def vocabulary_book(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    scope: str | None = None,
):
    return TEMPLATES.TemplateResponse(
        request, "practice/vocabulary.html", vocabulary_book_context(service, scope)
    )


#: 标记动作允许回到的页面；其它值一律忽略，避免变成开放重定向。
MARK_RETURNS = frozenset({"/practice/vocabulary", f"{REVIEW_BASE}?tab=words"})


@router.post("/practice/vocabulary/mark")
def mark_vocabulary(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    word: Annotated[str, Form()],
    action: Annotated[str, Form()] = "save",
    scope: Annotated[str | None, Form()] = None,
    return_to: Annotated[str | None, Form()] = None,
):
    if action == "clear":
        service.repository.clear_vocabulary_mark(word)
    elif action == "known":
        service.repository.set_vocabulary_mark(word, "known")
    else:
        service.repository.set_vocabulary_mark(word, "saved")
    if return_to in MARK_RETURNS:
        suffix = f"{'&' if '?' in return_to else '?'}scope={quote(scope)}" if scope else ""
        target = f"{return_to}{suffix}"
    else:
        target = f"/practice/vocabulary?scope={quote(scope)}" if scope else "/practice/vocabulary"
    return RedirectResponse(target, status_code=303)


def _vocabulary_export(rows: list[dict[str, Any]]) -> list[list[str]]:
    header = ["word", "pronunciation", "part_of_speech", "meaning_zh", "collocations", "example", "passages"]
    body = [
        [
            row["word"],
            row["pronunciation"] or "",
            row["part_of_speech"] or "",
            row["chinese_meaning"] or "",
            "; ".join(row["collocations"]),
            row["example"] or "",
            " | ".join(row["passages"]),
        ]
        for row in rows
    ]
    return [header, *body]


@router.get("/practice/mistakes/print")
def print_mistakes(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    type: str | None = None,
    explanations: int = 1,
    limit: int = 60,
):
    """错题本的打印版：一页一篇，可直接打印或存成 PDF。"""
    items = mistake_items(service)
    available = sorted({entry["type"].value for entry in items})
    selected = type if type in available else None
    visible = [entry for entry in items if selected is None or entry["type"].value == selected]
    capped = max(1, min(limit, 200))
    return TEMPLATES.TemplateResponse(
        request,
        "practice/mistakes_print.html",
        {
            "items": visible[:capped],
            "total_items": len(items),
            "shown": min(len(visible), capped),
            "selected_type": selected,
            "available_types": [
                {"value": value, "label": QUESTION_TYPE_LABELS[QuestionType(value)]}
                for value in available
            ],
            "with_explanations": explanations == 1,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
    )


@router.get("/practice/vocabulary/print")
def print_vocabulary(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    scope: str = "saved",
    mode: str = "list",
):
    """生词本的打印版：清单模式带释义，自测模式只留横线并把答案放在最后。"""
    scope = scope if scope in {"saved", "known", "all"} else "saved"
    mode = mode if mode in {"list", "quiz"} else "list"
    rows = _vocabulary_rows(service)
    if scope == "saved":
        visible = [row for row in rows if row["status"] == "saved"]
    elif scope == "known":
        visible = [row for row in rows if row["status"] == "known"]
    else:
        visible = rows
    return TEMPLATES.TemplateResponse(
        request,
        "practice/vocabulary_print.html",
        {
            "rows": visible,
            "scope": scope,
            "mode": mode,
            "total": len(visible),
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
    )


@router.get("/practice/vocabulary.csv")
def export_vocabulary_csv(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    scope: str = "saved",
):
    rows = [row for row in _vocabulary_rows(service) if scope != "saved" or row["status"] == "saved"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(_vocabulary_export(rows))
    payload = "\ufeff" + buffer.getvalue()  # BOM so Excel reads UTF-8
    return Response(
        payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vocabulary.csv"'},
    )


@router.get("/practice/vocabulary.md")
def export_vocabulary_markdown(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    scope: str = "saved",
):
    rows = [row for row in _vocabulary_rows(service) if scope != "saved" or row["status"] == "saved"]
    lines = ["# 雅思词汇本", ""]
    for row in rows:
        head = f"## {row['word']}"
        if row["pronunciation"]:
            head += f" /{row['pronunciation'].strip('/')}/"
        lines.append(head)
        if row["part_of_speech"] or row["chinese_meaning"]:
            lines.append(f"- 释义：{row['part_of_speech'] or ''} {row['chinese_meaning'] or ''}".strip())
        if row["collocations"]:
            lines.append(f"- 搭配：{'、'.join(row['collocations'])}")
        if row["example"]:
            lines.append(f"- 例句：{row['example']}")
        lines.append(f"- 出自：{'、'.join(row['passages'])}")
        lines.append("")
    return Response(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vocabulary.md"'},
    )


@router.get("/practice/{unit_id}/compare")
def practice_compare(
    request: Request,
    unit_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Side-by-side review: Chinese source, English passage and the brief coverage."""
    package = _completed_package(service, unit_id)
    try:
        source_text = service.source_text_for_unit(package.unit)
    except KeyError:
        source_text = ""
    source_paragraphs = [line.strip() for line in source_text.splitlines() if line.strip()]

    evidence: dict[str, list[int]] = {}
    for group in package.question_groups:
        for question in group.questions:
            evidence.setdefault(question.evidence_paragraph, []).append(question.number)

    brief_items = [*package.source_brief.core_facts, *package.source_brief.core_claims]
    texts = {item.id: item.text for item in brief_items}
    rows = [
        {
            "id": fact_id,
            "text": texts.get(fact_id, ""),
            "labels": list(labels or []),
        }
        for fact_id, labels in package.passage.source_coverage.items()
    ]
    rows.sort(key=lambda row: (bool(row["labels"]), row["id"]))
    labels_to_facts: dict[str, list[str]] = {}
    for row in rows:
        for label in row["labels"]:
            labels_to_facts.setdefault(label, []).append(row["id"])

    return TEMPLATES.TemplateResponse(
        request,
        "practice/compare.html",
        {
            "package": package,
            "unit": package.unit,
            "source_paragraphs": source_paragraphs,
            "source_characters": sum(len(item) for item in source_paragraphs),
            "evidence": evidence,
            "coverage_rows": rows,
            "labels_to_facts": labels_to_facts,
            "uncovered": [row for row in rows if not row["labels"]],
            "brief_count": len(brief_items),
            "question_count": sum(
                len(group.questions) for group in package.question_groups
            ),
        },
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


def _scored_attempts(
    service: ReadingStudioService,
    *,
    status: str = "submitted",
) -> list[dict[str, Any]]:
    """Submitted attempts with their package and per-question results, newest first."""
    units: dict[str, Any] = {}
    packages: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for attempt in service.repository.list_practice_attempts():
        if attempt["status"] != status:
            continue
        unit_id = attempt["unit_id"]
        if unit_id not in units:
            units[unit_id] = service.repository.get_unit(unit_id)
        unit = units[unit_id]
        if unit is None or unit.status != UnitStatus.COMPLETED:
            continue
        if unit_id not in packages:
            try:
                packages[unit_id] = service.load_package(unit_id)
            except (FileNotFoundError, ValueError):
                packages[unit_id] = None
        package = packages[unit_id]
        if package is None:
            continue
        answers = attempt["payload"].get("answers") or {}
        results, correct = _score_submission(package, answers)
        total = len(results)
        rows.append(
            {
                "attempt": attempt,
                "unit": unit,
                "package": package,
                "results": results,
                "correct": correct,
                "total": total,
                "accuracy": round(correct / total * 100) if total else 0,
                "elapsed": attempt["payload"].get("elapsed_seconds") or 0,
                "submitted_at": attempt["updated_at"],
            }
        )
    return rows


def _type_accuracy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per question type: how many were answered and how many were right."""
    buckets: dict[QuestionType, dict[str, Any]] = {}
    for row in rows:
        for result in row["results"]:
            question_type = next(
                group.type
                for group in row["package"].question_groups
                if any(question.number == result.number for question in group.questions)
            )
            bucket = buckets.setdefault(
                question_type,
                {
                    "label": QUESTION_TYPE_LABELS.get(
                        question_type, question_type.value
                    ),
                    "answered": 0,
                    "correct": 0,
                },
            )
            bucket["answered"] += 1
            bucket["correct"] += int(result.correct)
    return [
        {
            "type": question_type.value,
            "label": bucket["label"],
            "answered": bucket["answered"],
            "correct": bucket["correct"],
            "accuracy": round(bucket["correct"] / bucket["answered"] * 100),
        }
        for question_type, bucket in sorted(
            buckets.items(), key=lambda item: -item[1]["answered"]
        )
    ]


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
