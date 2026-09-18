"""Leitner 盒子排程：答对进一盒（间隔翻倍），答不完全对留在原地，答错退回第一盒。"""

from __future__ import annotations

import datetime as dt

# 第 1–5 盒的复习间隔（天）。基础 1 天起，最长 32 天。
BOX_INTERVALS: tuple[int, ...] = (1, 2, 4, 8, 32)
MAX_BOX = len(BOX_INTERVALS)
OUTCOMES = ("again", "hard", "good")
OUTCOME_LABELS = {"again": "不认识", "hard": "有点模糊", "good": "记住了"}


def interval_days(box: int) -> int:
    clamped = max(1, min(box, MAX_BOX))
    return BOX_INTERVALS[clamped - 1]


def next_box(box: int, outcome: str) -> int:
    current = max(1, min(box, MAX_BOX))
    if outcome == "again":
        return 1
    if outcome == "hard":
        return current
    return min(current + 1, MAX_BOX)


def due_date(box: int, *, now: dt.datetime | None = None, outcome: str = "good") -> dt.datetime:
    base = now or dt.datetime.now(dt.UTC)
    return base + dt.timedelta(days=interval_days(next_box(box, outcome)))


def schedule(
    state: dict | None,
    outcome: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    """根据当前状态和本次结果算出新的盒子、到期时间与计数。"""
    if outcome not in OUTCOMES:
        raise ValueError(f"Unsupported outcome: {outcome}")
    current_box = int(state["box"]) if state else 0
    seen = int(state.get("seen", 0)) if state else 0
    lapses = int(state.get("lapses", 0)) if state else 0
    starting_box = current_box or 1
    if outcome == "again":
        lapses += 1
    box = next_box(starting_box, outcome)
    moment = now or dt.datetime.now(dt.UTC)
    return {
        "box": box,
        "due_at": moment + dt.timedelta(days=interval_days(box)),
        "seen": seen + 1,
        "lapses": lapses,
        "interval_days": interval_days(box),
        "outcome": outcome,
    }
