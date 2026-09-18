"""词汇分类：词性、复现频率、来源篇目、难度，以及同根词与记忆联想。

分类全部基于本地已有数据（词的词性、释义、例句、出现篇目），不调用模型、
不查外部词典，所以随时可用且零成本。
"""

from __future__ import annotations

import re
from typing import Any

POS_GROUPS: dict[str, str] = {
    "noun": "名词",
    "verb": "动词",
    "adjective": "形容词",
    "adverb": "副词",
    "other": "其他 / 未标注",
}
POS_ORDER = ("noun", "verb", "adjective", "adverb", "other")

# 常见构词后缀：命中说明是"长词/抽象词"，通常更值得专门记。
HEAVY_SUFFIXES = (
    "tion",
    "sion",
    "ment",
    "ness",
    "ity",
    "ous",
    "ive",
    "ance",
    "ence",
    "ify",
    "ize",
    "ise",
    "ally",
    "ology",
)
# 屈折与派生后缀：剥掉它才能把 conserve / conservation / conserving 归到一族。
INFLECTIONS = tuple(
    sorted(
        (
            "ations",
            "ation",
            "tions",
            "tion",
            "sions",
            "sion",
            "ments",
            "ment",
            "nesses",
            "ness",
            "ities",
            "ity",
            "ically",
            "ical",
            "ally",
            "ings",
            "ing",
            "edly",
            "ed",
            "ly",
            "es",
            "s",
        ),
        key=len,
        reverse=True,
    )
)
LEVELS = ("基础", "进阶", "高阶")


def infer_pos(word: str) -> str:
    """没有 part_of_speech 时按词形推断词性（早期数据普遍缺这个字段）。

    只是给分类用，页面上会标注"推断"，不会写回数据库。
    """
    letters = re.sub(r"[^a-z]", "", word.strip().casefold())
    if letters.endswith("ly") and len(letters) > 4:
        return "adverb"
    if letters.endswith(("ous", "ive", "al", "ic", "able", "ible", "ful", "less")):
        return "adjective"
    if letters.endswith(("tion", "sion", "ment", "ness", "ity", "ance", "ence", "ship")):
        return "noun"
    if letters.endswith(("ize", "ise", "ify", "ate")):
        return "verb"
    return "other"


def pos_group(part_of_speech: str | None) -> str:
    """把 "n." / "adj" / "verb" 之类的标注归到五组之一。"""
    if not part_of_speech:
        return "other"
    tokens = {token for token in re.split(r"[^a-z]+", part_of_speech.casefold()) if token}
    if tokens & {"adj", "adjective", "adjectives"}:
        return "adjective"
    if tokens & {"adv", "adverb", "adverbs"}:
        return "adverb"
    if tokens & {"n", "noun", "nouns"}:
        return "noun"
    if tokens & {"v", "vt", "vi", "verb", "verbs"}:
        return "verb"
    return "other"


def difficulty_hint(word: str) -> dict[str, str]:
    """按词形给出一个粗粒度的难度提示（不是官方词频表，只用于排序取舍）。"""
    stripped = word.strip().casefold()
    letters = re.sub(r"[^a-z]", "", stripped)
    if not letters:
        return {"level": "进阶", "reason": "非字母词形"}
    if len(letters) <= 5:
        return {"level": "基础", "reason": f"{len(letters)} 个字母，短词"}
    if any(letters.endswith(suffix) for suffix in HEAVY_SUFFIXES) or len(letters) >= 10:
        suffix = next(
            (item for item in HEAVY_SUFFIXES if letters.endswith(item)), None
        )
        reason = f"{len(letters)} 个字母" + (f"，-{suffix} 抽象名词/形容词" if suffix else "")
        return {"level": "高阶", "reason": reason}
    return {"level": "进阶", "reason": f"{len(letters)} 个字母"}


def stem_of(word: str) -> str:
    """粗略词干：剥掉常见屈折/派生后缀与尾字母 e，低于 4 个字母就原样返回。

    conserve / conservation → conserv
    scarcity / scarce       → scarc
    """
    letters = re.sub(r"[^a-z]", "", word.strip().casefold())
    for suffix in INFLECTIONS:
        if letters.endswith(suffix) and len(letters) - len(suffix) >= 4:
            letters = letters[: -len(suffix)]
            break
    if letters.endswith("e") and len(letters) >= 5:
        letters = letters[:-1]
    return letters


def form_families(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同根/同形近词分组（至少有 2 个词才算一族）。"""
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stem = stem_of(row["word"])
        if len(stem) < 4:
            continue
        families.setdefault(stem, []).append(row)
    grouped = [
        {
            "stem": stem,
            "words": sorted(items, key=lambda item: item["word"].casefold()),
        }
        for stem, items in families.items()
        if len(items) > 1
    ]
    return sorted(grouped, key=lambda family: (-len(family["words"]), family["stem"]))


def related_words(row: dict[str, Any], rows: list[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    """这个词的"记忆挂钩"：同根词 + 同篇共现词。"""
    stem = stem_of(row["word"])
    passages = set(row["passages"])
    related: list[dict[str, str]] = []
    for other in rows:
        if other["key"] == row["key"]:
            continue
        other_stem = stem_of(other["word"])
        if len(stem) >= 4 and other_stem == stem:
            related.append({"word": other["word"], "why": "同根词"})
        elif passages & set(other["passages"]):
            related.append({"word": other["word"], "why": "同篇出现"})
    related.sort(key=lambda item: (item["why"] != "同根词", item["word"].casefold()))
    return related[:limit]


def classify_rows(
    rows: list[dict[str, Any]], review_state: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """一次算出页面上要用的所有分组与统计。"""
    review_state = review_state or {}
    by_pos: dict[str, list[dict[str, Any]]] = {key: [] for key in POS_ORDER}
    by_level: dict[str, list[dict[str, Any]]] = {level: [] for level in LEVELS}
    by_source: dict[str, list[dict[str, Any]]] = {}
    bands = {"高频复现（≥3 篇）": [], "出现过 2 次": [], "只出现 1 次": []}
    for row in rows:
        declared = pos_group(row.get("part_of_speech"))
        if declared == "other" and not (row.get("part_of_speech") or "").strip():
            inferred = infer_pos(row["word"])
            row["pos_inferred"] = inferred != "other"
            row["pos_key"] = inferred if inferred != "other" else "other"
        else:
            row["pos_inferred"] = False
            row["pos_key"] = declared
        by_pos[row["pos_key"]].append(row)
        row["pos_label"] = POS_GROUPS[row["pos_key"]]
        hint = difficulty_hint(row["word"])
        row["level"] = hint["level"]
        row["level_reason"] = hint["reason"]
        by_level[hint["level"]].append(row)
        for passage in row["passages"]:
            by_source.setdefault(passage, []).append(row)
        count = row.get("passage_count", len(row["passages"]))
        if count >= 3:
            bands["高频复现（≥3 篇）"].append(row)
        elif count == 2:
            bands["出现过 2 次"].append(row)
        else:
            bands["只出现 1 次"].append(row)
    for row in rows:
        state = review_state.get(row["key"])
        row["review_box"] = int(state["box"]) if state else 0
        row["due_at"] = state["due_at"] if state else None
        row["seen"] = int(state["seen"]) if state else 0
        row["lapses"] = int(state["lapses"]) if state else 0
    return {
        "by_pos": {
            key: {"label": POS_GROUPS[key], "rows": by_pos[key]} for key in POS_ORDER
        },
        "by_level": {
            level: {"label": level, "rows": by_level[level]} for level in LEVELS
        },
        "by_source": [
            {"label": source, "rows": items}
            for source, items in sorted(by_source.items(), key=lambda item: -len(item[1]))
        ],
        "by_frequency": [
            {"label": label, "rows": items} for label, items in bands.items()
        ],
        "families": form_families(rows),
    }
