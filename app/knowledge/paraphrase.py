"""同义替换线索：同一道题里"题干怎么说"对"原文怎么说"。

雅思阅读的核心技能就是认得出这层替换，所以这里把题干实词与原文证据句实词做一次
本地词干对齐，分三类呈现：

- ``form_pairs``：同根但词形不同（conserve → conserving），属于"看得懂但要注意形式"；
- ``prompt_markers``：题干里有、原文里换了说法的词（替换发生在题干侧）；
- ``quote_markers``：原文里有、题干里换了说法的词（替换发生在原文侧）。

不查词典、不调用模型，纯本地计算，因此可以随时重算且零成本。误判的代价只是
"高亮了一个普通词"，不会污染原始数据。
"""

from __future__ import annotations

from typing import Any

from app.vocabulary.classify import stem_of

from .text import content_words, unique_content_words

MIN_STEM = 4


def _unique(words: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for word in words:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered


def align(prompt: str, quote: str) -> dict[str, Any]:
    """把题干与原文证据句对齐，返回高亮所需的词表与配对。"""
    prompt_words = unique_content_words(prompt)
    quote_words = _unique(content_words(quote))
    quote_exact = set(quote_words)
    prompt_exact = set(prompt_words)
    prompt_stems = {stem_of(word) for word in prompt_words}

    quote_by_stem: dict[str, list[str]] = {}
    for word in quote_words:
        quote_by_stem.setdefault(stem_of(word), []).append(word)

    used_quote: set[str] = set()
    form_pairs: list[dict[str, str]] = []
    prompt_markers: list[str] = []
    for word in prompt_words:
        if word in quote_exact:
            used_quote.add(word)
            continue
        stem = stem_of(word)
        candidates = quote_by_stem.get(stem, []) if len(stem) >= MIN_STEM else []
        match = next((item for item in candidates if item not in used_quote), None)
        if match is None:
            prompt_markers.append(word)
        else:
            used_quote.add(match)
            form_pairs.append({"prompt": word, "quote": match})

    quote_markers = [
        word
        for word in quote_words
        if word not in used_quote
        and word not in prompt_exact
        and stem_of(word) not in prompt_stems
    ]

    if prompt_markers and quote_markers:
        hint = "题干与原文换了说法，靠意思对应（重点线索）"
    elif prompt_markers:
        hint = "题干改写了原文的表述"
    elif quote_markers:
        hint = "原文另有说法，题干用了原文里没有的词"
    elif form_pairs:
        hint = "题干基本照抄原文，只是词形有变化"
    else:
        hint = "题干与原文用词一致（可直接定位）"

    coverage = round(len(form_pairs) / len(prompt_words), 2) if prompt_words else 0.0
    return {
        "prompt_words": prompt_words,
        "quote_words": quote_words,
        "form_pairs": form_pairs,
        "prompt_markers": prompt_markers,
        "quote_markers": quote_markers,
        "coverage": coverage,
        "hint": hint,
    }


def question_note(
    *,
    unit_id: str,
    passage_title: str,
    difficulty: str,
    number: int,
    prompt: str,
    answer: str,
    evidence_label: str,
    evidence_quote: str,
    chinese_explanation: str,
    question_type: str = "",
    prompt_label: str = "题干",
    answer_label: str = "答案",
    prompt_raw: str = "",
    hint_override: str = "",
) -> dict[str, Any]:
    """一条"同义替换"知识点（一道题一条）。

    ``prompt`` 是真正拿来做对照的文本：匹配标题题的题干本身只是 "Paragraph A"，
    真正要对照的是选项里的标题，因此由调用方先用 ``prompt_label`` 说明这一侧是什么。
    """
    alignment = align(prompt, evidence_quote)
    return {
        "unit_id": unit_id,
        "passage_title": passage_title,
        "difficulty": difficulty,
        "number": number,
        "prompt": prompt,
        "prompt_label": prompt_label,
        "prompt_raw": prompt_raw or prompt,
        "answer": answer,
        "answer_label": answer_label,
        "evidence_label": evidence_label,
        "evidence_quote": evidence_quote,
        "chinese_explanation": chinese_explanation,
        "question_type": question_type,
        **alignment,
        **({"hint": hint_override} if hint_override else {}),
    }
