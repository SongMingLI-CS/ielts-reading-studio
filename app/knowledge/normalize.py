"""知识点条目的统一口径：kind、难度等级、词性标签。

两边的数据来源表示方式不同（阅读侧的词汇表只有 ``n.`` 这类简写、没有 CEFR；
小说侧的术语有 CEFR 但词性写法更自由），这里把差异抹平，页面上就只有一套口径。
"""

from __future__ import annotations

from typing import Any

from app.vocabulary.classify import POS_GROUPS, difficulty_hint, infer_pos, pos_group

from .text import is_phrase

LEVELS = ("B1", "B2", "C1")
LEVEL_LABELS = {"B1": "B1 基础", "B2": "B2 进阶", "C1": "C1 高阶"}
KIND_LABELS = {
    "word": "单词",
    "collocation": "固定搭配",
    "phrasal": "短语动词",
}
KIND_ORDER = ("word", "collocation", "phrasal")
_HINT_TO_LEVEL = {"基础": "B1", "进阶": "B2", "高阶": "C1"}
_PARTICLES = {
    "about",
    "across",
    "after",
    "against",
    "along",
    "around",
    "at",
    "away",
    "back",
    "by",
    "down",
    "for",
    "forth",
    "from",
    "in",
    "into",
    "of",
    "off",
    "on",
    "onto",
    "out",
    "over",
    "round",
    "through",
    "to",
    "together",
    "toward",
    "towards",
    "up",
    "upon",
    "with",
    "without",
}
_DETERMINERS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "his",
    "her",
    "its",
    "their",
    "your",
    "my",
    "our",
}


def resolve_level(word: str, cefr: str | None) -> dict[str, str]:
    """等级：有 CEFR 就用 CEFR，否则按词形估一个并在页面上标注"按词形推断"。"""
    declared = (cefr or "").strip().upper()
    if declared in LEVELS:
        return {"level": declared, "level_source": "cefr", "level_reason": "术语库标注"}
    hint = difficulty_hint(word)
    return {
        "level": _HINT_TO_LEVEL.get(hint["level"], "B2"),
        "level_source": "heuristic",
        "level_reason": hint["reason"],
    }


def resolve_pos(word: str, part_of_speech: str | None) -> dict[str, Any]:
    """词性：能识别就用标注，否则按词形推断并标注来源。"""
    declared = (part_of_speech or "").strip()
    group = pos_group(declared)
    inferred = False
    if not declared:
        guessed = infer_pos(word)
        group = guessed if guessed != "other" else "other"
        inferred = True
    return {
        "pos_key": group,
        "pos_label": POS_GROUPS[group],
        "pos_declared": declared,
        "pos_inferred": inferred,
    }


def kind_of(word: str, part_of_speech: str | None) -> str:
    """单词 / 固定搭配 / 短语动词。"""
    cleaned = (word or "").strip()
    if not is_phrase(cleaned):
        return "word"
    lowered = cleaned.casefold()
    pieces = lowered.replace("-", " ").split()
    declared = (part_of_speech or "").casefold()
    looks_verbal = "verb" in declared or "phrase" not in declared
    if (
        len(pieces) >= 2
        and pieces[-1] in _PARTICLES
        and pieces[0] not in _DETERMINERS
        and looks_verbal
        and not declared.startswith("noun")
    ):
        return "phrasal"
    return "collocation"
