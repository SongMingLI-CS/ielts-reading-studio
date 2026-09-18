"""文本工具：分词、停用词、词形匹配与例句高亮。

知识点汇总里的"同义替换"和"例句高亮"都只依赖这里，不查外部词典、不调用模型，
所以离线可用、随时可重算。
"""

from __future__ import annotations

import html
import re

from markupsafe import Markup

# 功能词与题型套话：它们出现在题干里不代表"生词"，出现在原文里也不能算"原文措辞"。
# 用 findall 而不是 str.split：排版上能按行读，也不会被 linter 拆成上百行。
_STOPWORD_TEXT = """
a an the and or but nor so yet if then than that this these those there here
of in on at to for from by with without within into onto over under about above below
is are was were be been being am do does did done doing have has had having
will would shall should can could may might must
i you he she it we they me him her us them his her its their our your my
as because while when where which who whom whose what how why whether
not no all any both each few more most other some such only own same too very
also just even ever never always often sometimes usually already still almost
one two three four five six seven eight nine ten first second third
according author writer passage paragraph text statement following choose answer
true false given yes correct letter question questions option options best
mention mentioned mentions describe described
"""
STOPWORDS = frozenset(re.findall(r"\S+", _STOPWORD_TEXT))

# 字母词与数字都算：题干里的 "three" 与原文的 "3" 是同一处的两种说法，值得对照。
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*|\d+(?:[.,]\d+)?")
_VOWELS = frozenset("aeiou")


def tokens(text: str) -> list[str]:
    """字母词与数字序列，字母统一小写。"""
    return [match.group(0).casefold() for match in TOKEN_PATTERN.finditer(text or "")]


def content_words(text: str) -> list[str]:
    """实词序列（去停用词、去单字母），保留重复与数字。"""
    words: list[str] = []
    for match in TOKEN_PATTERN.finditer(text or ""):
        raw = match.group(0)
        if raw[0].isdigit():
            words.append(raw)
            continue
        word = raw.casefold()
        if len(word) > 1 and word not in STOPWORDS:
            words.append(word)
    return words


def unique_content_words(text: str) -> list[str]:
    """实词序列去重，保留首次出现顺序。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for word in content_words(text):
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered


def is_phrase(value: str) -> bool:
    """含空格或连字符的多词条目，通常就是固定搭配。"""
    cleaned = (value or "").strip()
    return bool(cleaned) and (" " in cleaned or "-" in cleaned)


def _stem_pattern(cleaned: str) -> str:
    """词干正则：覆盖 conserve/conserving/conserved、supply/supplies、plan/planning 这类形式。"""
    letters = re.sub(r"[^a-z]", "", cleaned.casefold())
    if letters.endswith("e") and len(letters) > 3:
        return re.escape(letters[:-1]) + "e?"
    if letters.endswith("y") and len(letters) > 3 and letters[-2] not in _VOWELS:
        return re.escape(letters[:-1]) + "(?:y|ies|ied)"
    if (
        len(letters) >= 3
        and letters[-1] not in _VOWELS
        and letters[-1] not in "wxy"
        and letters[-2] in _VOWELS
        and letters[-3] not in _VOWELS
    ):
        # 短词会重写末辅音：plan → plann(ing) / plann(ed)
        return re.escape(letters) + re.escape(letters[-1]) + "?"
    return re.escape(letters)


def form_pattern(term: str) -> re.Pattern[str]:
    """匹配一个词及其常见屈折形式，或一个多词搭配（允许空格数量变化）。"""
    cleaned = (term or "").strip()
    if not cleaned:
        return re.compile(r"(?!)")
    if is_phrase(cleaned):
        parts = [re.escape(piece) for piece in cleaned.split()]
        return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)
    letters = re.sub(r"[^a-z]", "", cleaned.casefold())
    if letters.endswith("y") and len(letters) > 3 and letters[-2] not in _VOWELS:
        # y / ies / ied 已在词干里覆盖，不再追加后缀
        return re.compile(rf"\b{_stem_pattern(cleaned)}\b", re.IGNORECASE)
    return re.compile(rf"\b{_stem_pattern(cleaned)}(?:s|es|d|ed|ing)?\b", re.IGNORECASE)


def highlight(text: str, terms: list[str], css_class: str = "kw") -> Markup:
    """把例句里的目标词/搭配包成 ``<mark>``，返回可直接渲染的安全 HTML。"""
    source = text or ""
    spans: list[tuple[int, int]] = []
    for term in terms:
        if not (term or "").strip():
            continue
        for match in form_pattern(term).finditer(source):
            spans.append((match.start(), match.end()))
    if not spans:
        return Markup(html.escape(source))
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(html.escape(source[cursor:start]))
        pieces.append(
            f'<mark class="{css_class}">{html.escape(source[start:end])}</mark>'
        )
        cursor = end
    pieces.append(html.escape(source[cursor:]))
    return Markup("".join(pieces))


def mark_words(text: str, words: list[str], css_class: str) -> Markup:
    """把文本里出现的指定词包成 ``<mark>``（用于题干/原文的措辞对照）。"""
    return highlight(text, words, css_class)
