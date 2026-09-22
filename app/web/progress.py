"""练习进度的小工具：题号、某题是否答过、草稿答到第几题。

首页的"继续第 N 题"与练习中心的"继续第 N 题"必须给出同一个数字，判定逻辑因此放在
一处，而不是各写一份。草稿的 payload 是前端直接写进数据库的 JSON，多选存列表、文本题
存字符串，这里统一按"有内容就算答过"处理。
"""

from __future__ import annotations

from typing import Any

from app.models import ReadingPackage


def question_numbers(package: ReadingPackage) -> list[int]:
    return [
        question.number
        for group in package.question_groups
        for question in group.questions
    ]


def has_answer(value: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def draft_progress(
    package: ReadingPackage, payload: dict[str, Any] | None
) -> dict[str, int | None]:
    """答了几题、共几题、下一题是第几题（都答完则为 None）。"""

    numbers = question_numbers(package)
    answers = (payload or {}).get("answers") or {}
    answered = sum(1 for number in numbers if has_answer(answers.get(str(number))))
    upcoming = next(
        (number for number in numbers if not has_answer(answers.get(str(number)))), None
    )
    return {"answered": answered, "total": len(numbers), "upcoming": upcoming}
