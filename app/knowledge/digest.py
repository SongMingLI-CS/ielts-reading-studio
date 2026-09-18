"""知识点汇总：把阅读侧与小说侧的条目合成一份可筛选、可打印的手册。

合并没有调用模型，全部是本地整理：

- 同一个词两边都出现时合并成一条，但保留两侧的来源与出现次数（阅读按"篇"，小说按"章次"）；
- CEFR 等级优先用小说术语库的标注，阅读侧没有标注的按词形估一个并标记来源；
- 固定搭配从两侧的 ``collocations`` 摊平成一个独立视图，便于集中记搭配。
"""

from __future__ import annotations

from typing import Any

from .normalize import KIND_LABELS, KIND_ORDER, LEVEL_LABELS, LEVELS
from .novel import load_novel_terms
from .reading import load_reading_terms

VIEWS = ("terms", "collocations", "paraphrase")
VIEW_LABELS = {
    "terms": "词汇精讲",
    "collocations": "固定搭配",
    "paraphrase": "同义替换",
}
SORTS = ("alpha", "level", "repeats", "source")
SORT_LABELS = {
    "alpha": "字母序",
    "level": "难度",
    "repeats": "复现次数",
    "source": "来源",
}
PAGE_SIZES = {"terms": 48, "collocations": 60, "paraphrase": 20}
_LEVEL_RANK = {"C1": 0, "B2": 1, "B1": 2}


def _merge_term(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """把同 key 的条目并进已存在的行（字段取信息更全的一方）。"""
    if not target["meaning"]:
        target["meaning"] = incoming["meaning"]
    if not target["phonetic"]:
        target["phonetic"] = incoming["phonetic"]
    if len(incoming["example"]) > len(target["example"]):
        target["example"] = incoming["example"]
    if target["level_source"] != "cefr" and incoming["level_source"] == "cefr":
        target["level"] = incoming["level"]
        target["level_source"] = incoming["level_source"]
        target["level_reason"] = incoming["level_reason"]
    if target["kind"] == "word" and incoming["kind"] != "word":
        target["kind"] = incoming["kind"]
    if target["pos_inferred"] and not incoming["pos_inferred"]:
        target["pos_key"] = incoming["pos_key"]
        target["pos_label"] = incoming["pos_label"]
        target["pos_declared"] = incoming["pos_declared"]
        target["pos_inferred"] = False
    for collocation in incoming["collocations"]:
        if collocation and collocation not in target["collocations"]:
            target["collocations"].append(collocation)
    for context in incoming["contexts"]:
        if context not in target["contexts"]:
            target["contexts"].append(context)
    known = {item["href"] for item in target["sources"]}
    for source in incoming["sources"]:
        if source["href"] not in known:
            target["sources"].append(source)
            known.add(source["href"])
    target["source_counts"]["reading"] += incoming["source_counts"]["reading"]
    target["source_counts"]["novel"] += incoming["source_counts"]["novel"]
    target["occurrences"] = (
        target["source_counts"]["reading"] + target["source_counts"]["novel"]
    )
    target["status"] = target.get("status") or incoming.get("status", "")


def merge_terms(
    reading_terms: list[dict[str, Any]], novel_terms: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """两侧词汇合并成一份（同 key 归并，来源与出现次数分别记录）。"""
    merged: dict[str, dict[str, Any]] = {}
    pairs = [(row, "reading") for row in reading_terms] + [
        (row, "novel") for row in novel_terms
    ]
    for row, side in pairs:
        counts = {"reading": 0, "novel": 0}
        counts[side] = int(row.get("occurrences") or 0)
        candidate = {
            **row,
            "status": row.get("status", ""),
            "source_counts": counts,
            "occurrences": counts["reading"] + counts["novel"],
        }
        existing = merged.get(row["key"])
        if existing is None:
            merged[row["key"]] = candidate
        else:
            _merge_term(existing, candidate)
    return list(merged.values())


def source_kinds(row: dict[str, Any]) -> set[str]:
    return {item["kind"] for item in row["sources"]}


def collocation_rows(terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把搭配摊平：一条搭配一张卡，指回它所属的单词。"""
    rows: dict[str, dict[str, Any]] = {}
    for term in terms:
        for collocation in term["collocations"]:
            phrase = collocation.strip()
            if not phrase:
                continue
            row = rows.setdefault(
                phrase.casefold(),
                {
                    "key": phrase.casefold(),
                    "headword": phrase,
                    "headword_key": term["key"],
                    "word": term["headword"],
                    "meaning": term["meaning"],
                    "example": term["example"],
                    "example_sentence": term["example"],
                    "level": term["level"],
                    "level_source": term["level_source"],
                    "level_reason": term["level_reason"],
                    "kind": "collocation",
                    "pos_key": "other",
                    "pos_label": "固定搭配",
                    "pos_inferred": True,
                    "collocations": [phrase],
                    "sources": list(term["sources"]),
                    "source_counts": dict(term["source_counts"]),
                    "occurrences": term["occurrences"],
                    "contexts": list(term["contexts"]),
                    "status": term.get("status", ""),
                },
            )
            known = {item["href"] for item in row["sources"]}
            for source in term["sources"]:
                if source["href"] not in known:
                    row["sources"].append(source)
                    known.add(source["href"])
    return list(rows.values())


def _matches(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    haystack = " ".join(
        [
            str(row.get("headword") or ""),
            str(row.get("word") or ""),
            str(row.get("meaning") or ""),
            str(row.get("example") or ""),
            " ".join(row.get("collocations", [])),
            " ".join(str(item.get("zh", "")) for item in row.get("contexts", [])),
        ]
    ).casefold()
    return needle in haystack


def filter_terms(
    rows: list[dict[str, Any]],
    *,
    source: str = "all",
    kind: str = "all",
    level: str = "all",
    pos: str = "all",
    query: str = "",
    has_collocation: bool = False,
    repeats: bool = False,
) -> list[dict[str, Any]]:
    """按页面筛选条过滤（条件之间是"与"关系）。"""
    selected: list[dict[str, Any]] = []
    for row in rows:
        kinds = source_kinds(row)
        if source == "reading" and "reading" not in kinds:
            continue
        if source == "novel" and "novel" not in kinds:
            continue
        if source == "both" and len(kinds) < 2:
            continue
        if kind != "all" and row["kind"] != kind:
            continue
        if level != "all" and row["level"] != level:
            continue
        if pos != "all" and row.get("pos_key") != pos:
            continue
        if has_collocation and not row["collocations"]:
            continue
        if repeats and row["occurrences"] < 2:
            continue
        if not _matches(row, query):
            continue
        selected.append(row)
    return selected


def sort_terms(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "level":
        return sorted(
            rows,
            key=lambda row: (
                _LEVEL_RANK.get(row["level"], 3),
                row["headword"].casefold(),
            ),
        )
    if sort == "repeats":
        return sorted(
            rows,
            key=lambda row: (-int(row["occurrences"]), row["headword"].casefold()),
        )
    if sort == "source":
        return sorted(
            rows,
            key=lambda row: (
                ",".join(sorted(source_kinds(row))),
                row["headword"].casefold(),
            ),
        )
    return sorted(rows, key=lambda row: row["headword"].casefold())


def paginate(rows: list[dict[str, Any]], page: int, size: int) -> dict[str, Any]:
    total = len(rows)
    size = max(1, min(size, 200))
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(page, pages))
    start = (current - 1) * size
    window = rows[start : start + size]
    return {
        "rows": window,
        "total": total,
        "page": current,
        "pages": pages,
        "size": size,
        "has_prev": current > 1,
        "has_next": current < pages,
        "range_label": f"{start + 1}–{start + len(window)}" if total else "0",
    }


def bucket_counts(rows: list[dict[str, Any]], values) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for row in rows:
        for value in values(row):
            buckets[value] = buckets.get(value, 0) + 1
    return buckets


def note_level(note: dict[str, Any]) -> str:
    """题目本身没有等级，用所属篇目的难度折算一个粗粒度等级。"""
    mapping = {"advanced": "C1", "standard": "B2", "foundation": "B1"}
    return mapping.get(note.get("difficulty", ""), "B2")


def matches_note(note: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    haystack = " ".join(
        [
            note["prompt"],
            note["evidence_quote"],
            note["answer"],
            note["chinese_explanation"],
            note["passage_title"],
        ]
    ).casefold()
    return needle in haystack


def load_paraphrase_notes(service: Any) -> list[dict[str, Any]]:
    """同义替换线索（每次都重新读包：包数量与体量都很小）。"""
    from .reading import load_question_notes

    return load_question_notes(service)


def build_digest(service: Any, **options: Any) -> dict[str, Any]:
    """页面需要的全部内容：统计、筛选项、当前页数据。"""
    view = options.get("view") if options.get("view") in VIEWS else "terms"
    reading = load_reading_terms(service)
    novel = load_novel_terms(service.config)
    terms = merge_terms(reading["terms"], novel["terms"])
    collocations = collocation_rows(terms)
    notes = load_paraphrase_notes(service)

    source = options.get("source") or "all"
    kind = options.get("kind") or "all"
    level = options.get("level") or "all"
    pos = options.get("pos") or "all"
    query = (options.get("q") or "").strip()
    has_collocation = bool(options.get("has_collocation"))
    repeats = bool(options.get("repeats"))
    sort = options.get("sort") if options.get("sort") in SORTS else "alpha"
    size = int(options.get("size") or PAGE_SIZES[view])
    page = int(options.get("page") or 1)

    if view == "terms":
        filtered = sort_terms(
            filter_terms(
                terms,
                source=source,
                kind=kind,
                level=level,
                pos=pos,
                query=query,
                has_collocation=has_collocation,
                repeats=repeats,
            ),
            sort,
        )
    elif view == "collocations":
        filtered = sort_terms(
            filter_terms(collocations, source=source, level=level, query=query),
            sort,
        )
    else:
        filtered = [
            note
            for note in notes
            if matches_note(note, query)
            and (level == "all" or note_level(note) == level)
        ]
    pages = paginate(filtered, page, size)

    stats = {
        "terms": len(terms),
        "collocations": len(collocations),
        "paraphrase": len(notes),
        "reading_units": reading["unit_count"],
        "novel_chapters": novel["chapter_count"],
        "novel_available": novel["available"],
        "with_collocation": sum(1 for row in terms if row["collocations"]),
        "repeated": sum(1 for row in terms if row["occurrences"] >= 2),
        "both_sources": sum(1 for row in terms if len(source_kinds(row)) > 1),
        "with_phonetic": sum(1 for row in terms if row["phonetic"]),
        "phrase_terms": sum(1 for row in terms if row["kind"] != "word"),
    }
    kinds_present = bucket_counts(terms, lambda row: [row["kind"]])
    levels_present = bucket_counts(terms, lambda row: [row["level"]])
    return {
        "view": view,
        "view_label": VIEW_LABELS[view],
        "view_counts": {
            "terms": stats["terms"],
            "collocations": stats["collocations"],
            "paraphrase": stats["paraphrase"],
        },
        "rows": pages["rows"],
        "pagination": pages,
        "stats": stats,
        "filters": {
            "view": view,
            "source": source,
            "kind": kind,
            "level": level,
            "pos": pos,
            "q": query,
            "has_collocation": has_collocation,
            "repeats": repeats,
            "sort": sort,
            "size": size,
        },
        "options": {
            "levels": [item for item in LEVELS if item in levels_present],
            "kinds": [item for item in KIND_ORDER if item in kinds_present],
            "pos": sorted(bucket_counts(terms, lambda row: [row["pos_key"]])),
        },
        "source_counts": {
            "reading": sum(1 for row in terms if "reading" in source_kinds(row)),
            "novel": sum(1 for row in terms if "novel" in source_kinds(row)),
            "both": stats["both_sources"],
        },
        "labels": {
            "kinds": KIND_LABELS,
            "levels": LEVEL_LABELS,
            "views": VIEW_LABELS,
            "sorts": SORT_LABELS,
        },
    }
