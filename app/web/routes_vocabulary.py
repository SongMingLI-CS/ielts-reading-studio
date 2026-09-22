from __future__ import annotations

import datetime as dt
import random
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.pipeline.service import ReadingStudioService
from app.vocabulary import (
    classify_rows,
    due_rows,
    interval_days,
    related_words,
    schedule,
    study_rows,
    tracked_rows,
)
from app.vocabulary.srs import MAX_BOX, OUTCOME_LABELS

from .dependencies import get_service
from .templating import templates

router = APIRouter()
TEMPLATES = templates()

GROUPS = ("pos", "frequency", "source", "level", "family")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _options_for(
    row: dict[str, Any], pool: list[dict[str, Any]], rng: random.Random
) -> list[str]:
    """四选一的释义选项：正确答案 + 3 个其它词的释义。"""
    correct = (row["chinese_meaning"] or "").strip()
    distractors: list[str] = []
    others = [item for item in pool if item["key"] != row["key"]]
    rng.shuffle(others)
    for item in others:
        meaning = (item["chinese_meaning"] or "").strip()
        if meaning and meaning != correct and meaning not in distractors:
            distractors.append(meaning)
        if len(distractors) == 3:
            break
    if not correct or len(distractors) < 3:
        return []
    options = [correct, *distractors]
    rng.shuffle(options)
    return options


@router.get("/vocabulary")
def vocabulary_home(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    group: str = "pos",
    scope: str = "saved",
    focus: str | None = None,
    meanings: str = "all",
):
    now = _now()
    rows = study_rows(service)
    scope = scope if scope in {"saved", "tracked", "all"} else "saved"
    if scope == "saved":
        scoped = [row for row in rows if row["status"] == "saved"]
    elif scope == "tracked":
        scoped = tracked_rows(rows)
    else:
        scoped = rows
    if meanings == "with":
        # 早期生成的词条可能只有 word、没有释义；这类词背不了，但删掉可惜。
        scoped = [row for row in scoped if (row["chinese_meaning"] or "").strip()]
    grouped = classify_rows(rows)
    selected_group = group if group in GROUPS else "pos"
    due = due_rows(rows, now)
    focus_row = next(
        (row for row in rows if row["key"] == (focus or "").casefold()), None
    )
    if focus_row is not None:
        focus_row["related"] = related_words(focus_row, rows)
    return TEMPLATES.TemplateResponse(
        request,
        "vocabulary/index.html",
        {
            "group_sections": {
                "pos": list(grouped["by_pos"].values()),
                "frequency": grouped["by_frequency"],
                "source": grouped["by_source"],
                "level": list(grouped["by_level"].values()),
                "family": grouped["families"],
            }[selected_group],
            "selected_group": selected_group,
            "group_tabs": [
                ("pos", "按词性"),
                ("frequency", "按复现频率"),
                ("source", "按来源篇目"),
                ("level", "按难度"),
                ("family", "同根词族"),
            ],
            "rows": scoped[:80],
            "scope": scope,
            "meanings": meanings if meanings in {"all", "with"} else "all",
            "totals": {
                "all": len(rows),
                "saved": sum(row["status"] == "saved" for row in rows),
                "known": sum(row["status"] == "known" for row in rows),
                "tracked": len(tracked_rows(rows)),
                "due": len(due),
                "families": len(grouped["families"]),
                "no_meaning": sum(
                    not (row["chinese_meaning"] or "").strip() for row in rows
                ),
            },
            "box_counts": {
                box: sum(row["review_box"] == box for row in rows)
                for box in range(1, MAX_BOX + 1)
            },
            "box_intervals": {
                box: interval_days(box) for box in range(1, MAX_BOX + 1)
            },
            "focus": focus_row,
            "due_preview": due[:6],
            "outcome_labels": OUTCOME_LABELS,
            "max_box": MAX_BOX,
        },
    )


@router.get("/vocabulary/review")
def vocabulary_review(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    mode: str = "due",
    feedback: str | None = None,
    answer: str | None = None,
    last: str | None = None,
):
    """复习会话：一次一张卡，四选一；答完立刻排下一次复习时间。"""
    now = _now()
    all_rows = study_rows(service)
    rows = tracked_rows(all_rows)
    mode = mode if mode in {"due", "all"} else "due"
    pool = rows if mode == "all" else due_rows(rows, now)
    pool = sorted(pool, key=lambda row: (row["due_at"] or now, row["word"].casefold()))
    card = pool[0] if pool else None
    options: list[str] = []
    if card is not None:
        seed = f"{card['key']}:{int(now.timestamp()) // 600}"
        # 干扰项从整个词库取，否则队列里只有一个词时出不了选择题。
        options = _options_for(card, all_rows, random.Random(seed))
        card["related"] = related_words(card, all_rows)
    return TEMPLATES.TemplateResponse(
        request,
        "vocabulary/review.html",
        {
            "card": card,
            "options": options,
            "mode": mode,
            "remaining": max(0, len(pool) - (1 if card else 0)),
            "due_total": len(due_rows(rows, now)),
            "tracked_total": len(rows),
            "feedback": feedback,
            "answered": answer,
            "last_word": last,
            "outcome_labels": OUTCOME_LABELS,
            "box_intervals": {
                box: interval_days(box) for box in range(1, MAX_BOX + 1)
            },
        },
    )


@router.post("/vocabulary/review/answer")
def answer_card(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    word: Annotated[str, Form()],
    choice: Annotated[str, Form()] = "",
    mode: Annotated[str, Form()] = "due",
):
    """四选一的作答：选项就是释义文本，选错按"不认识"处理。"""
    row = _find(study_rows(service), word)
    correct = (row["chinese_meaning"] or "").strip()
    outcome = "good" if correct and choice.strip() == correct else "again"
    _apply(service, row, outcome)
    return RedirectResponse(
        "/vocabulary/review?"
        f"mode={quote(mode)}&feedback={'right' if outcome == 'good' else 'wrong'}"
        f"&answer={quote(correct)}&last={quote(row['word'])}",
        status_code=303,
    )


@router.post("/vocabulary/review/grade")
def grade_card(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    word: Annotated[str, Form()],
    outcome: Annotated[str, Form()] = "good",
    mode: Annotated[str, Form()] = "due",
):
    """自评模式：认识 / 模糊 / 不认识。"""
    if outcome not in OUTCOME_LABELS:
        raise HTTPException(status_code=422, detail="Unsupported outcome")
    row = _find(study_rows(service), word)
    _apply(service, row, outcome)
    return RedirectResponse(
        "/vocabulary/review?"
        f"mode={quote(mode)}&feedback={quote(outcome)}&last={quote(row['word'])}",
        status_code=303,
    )


@router.post("/vocabulary/track")
def track_word(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    word: Annotated[str, Form()],
    action: Annotated[str, Form()] = "add",
    scope: Annotated[str | None, Form()] = None,
):
    """把词加入/移出复习队列；加入时立即到期，可以马上开始背。"""
    key = word.strip().casefold()
    if not key:
        raise HTTPException(status_code=422, detail="Word is required")
    if action == "remove":
        service.repository.clear_vocabulary_review(key)
    elif service.repository.get_vocabulary_review(key) is None:
        service.repository.upsert_vocabulary_review(
            key, box=1, due_at=_now(), seen=0, lapses=0
        )
    target = "/vocabulary" + (f"?scope={quote(scope)}" if scope else "")
    return RedirectResponse(target, status_code=303)


@router.post("/vocabulary/review/reset")
def reset_word(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    word: Annotated[str, Form()],
):
    service.repository.clear_vocabulary_review(word.strip().casefold())
    return RedirectResponse("/vocabulary/review?feedback=reset", status_code=303)


def _find(rows: list[dict[str, Any]], word: str) -> dict[str, Any]:
    key = word.strip().casefold()
    row = next((item for item in rows if item["key"] == key), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Word not found in the vocabulary")
    return row


def _apply(
    service: ReadingStudioService, row: dict[str, Any], outcome: str
) -> dict[str, Any]:
    """按 Leitner 规则更新复习状态并落库。"""
    state = service.repository.get_vocabulary_review(row["key"])
    result = schedule(state, outcome)
    service.repository.upsert_vocabulary_review(
        row["key"],
        box=int(result["box"]),
        due_at=result["due_at"],
        seen=int(result["seen"]),
        lapses=int(result["lapses"]),
    )
    return result
