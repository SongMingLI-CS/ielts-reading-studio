"""把已完成的 Package 汇总成词表：跨篇合并、去重、带上收藏与复习状态。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.models import UnitStatus


def collect_vocabulary(service: Any, *, include_marks: bool = True) -> list[dict[str, Any]]:
    """全部词条，按"出现篇数多、字母序"排序。

    同一个词在不同篇目里出现时合并为一条，保留第一个非空释义/音标/例句，
    搭配则取并集；``passage_count`` 就是复现次数，是判断"该不该背"的主要依据。
    """
    marks = service.repository.list_vocabulary_marks() if include_marks else {}
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
                        "key": key,
                        "pronunciation": entry.pronunciation,
                        "part_of_speech": entry.part_of_speech,
                        "chinese_meaning": entry.chinese_meaning,
                        "collocations": list(entry.collocations),
                        "example": entry.example,
                        "passages": [],
                        "unit_ids": [],
                        "status": marks.get(key, ""),
                    },
                )
                if package.passage.title not in row["passages"]:
                    row["passages"].append(package.passage.title)
                if unit.id not in row["unit_ids"]:
                    row["unit_ids"].append(unit.id)
                row["pronunciation"] = row["pronunciation"] or entry.pronunciation
                row["part_of_speech"] = row["part_of_speech"] or entry.part_of_speech
                row["chinese_meaning"] = row["chinese_meaning"] or entry.chinese_meaning
                row["example"] = row["example"] or entry.example
                for collocation in entry.collocations:
                    if collocation not in row["collocations"]:
                        row["collocations"].append(collocation)
    rows = sorted(
        merged.values(),
        key=lambda row: (-len(row["passages"]), row["word"].casefold()),
    )
    for row in rows:
        row["passage_count"] = len(row["passages"])
    return rows


def as_aware(value: Any) -> dt.datetime | None:
    """SQLite 取回的时间可能没有时区，统一按 UTC 补齐再比较。"""

    if not isinstance(value, dt.datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def study_rows(service: Any) -> list[dict[str, Any]]:
    """全部词条 + 复习状态（box / 到期时间 / 复习次数）。

    首页与词汇页都要判断"今天该复习哪些词"，判定逻辑必须只有一份，否则两处会
    对同一个词给出不同的到期待办。
    """

    rows = collect_vocabulary(service)
    reviews = service.repository.list_vocabulary_reviews()
    for row in rows:
        state = reviews.get(row["key"])
        row["review_box"] = int(state["box"]) if state else 0
        row["due_at"] = as_aware(state["due_at"]) if state else None
        row["seen"] = int(state["seen"]) if state else 0
        row["lapses"] = int(state["lapses"]) if state else 0
    return rows


def tracked_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已经进入复习队列的词（box > 0）；收藏了但还没开始复习的不算。"""

    return [row for row in rows if row["review_box"] > 0]


def due_rows(rows: list[dict[str, Any]], now: dt.datetime) -> list[dict[str, Any]]:
    """今天该复习的词：在队列里，且到期时间已过或从未排期。"""

    return [
        row
        for row in tracked_rows(rows)
        if row["due_at"] is None or row["due_at"] <= now
    ]
