from __future__ import annotations

import pytest

from app.parsers.chapter_detection import (
    HeadingKind,
    detect_heading,
    parse_chinese_number,
)


@pytest.mark.parametrize("title", [
    "第1章 初见", "第一章 初见", "第001章 初见", "Chapter 12 Arrival",
    "卷二 风起", "序章", "终章", "番外 海边",
])
def test_recognizes_supported_titles(title):
    assert detect_heading(title) is not None


def test_rejects_sentence_that_only_mentions_a_chapter():
    assert detect_heading("他在第一章中已经解释过原因。") is None


def test_reports_kind_and_ordinal_for_supported_titles():
    assert detect_heading("第一章 初见").ordinal == 1
    assert detect_heading("第001章 初见").ordinal == 1
    assert detect_heading("第十二章 结果").ordinal == 12
    assert detect_heading("第二十一章 结果").ordinal == 21
    assert detect_heading("Chapter 12 Arrival").ordinal == 12
    assert detect_heading("卷二 风起").kind == HeadingKind.VOLUME
    assert detect_heading("卷一 风起").ordinal == 1
    assert detect_heading("序章").kind == HeadingKind.PREFACE
    assert detect_heading("终章").kind == HeadingKind.EPILOGUE
    assert detect_heading("番外 海边").kind == HeadingKind.EXTRA


def test_normalizes_whitespace_in_titles():
    heading = detect_heading("  第 1 章   初见  ")
    assert heading is not None
    assert heading.title == "第 1 章 初见"


def test_ignores_empty_long_and_sentence_like_lines():
    assert detect_heading("") is None
    assert detect_heading("   ") is None
    assert detect_heading("第一章 " + "甲" * 60) is None
    assert detect_heading("第1章 讲的是他离开的原因，随后发生了一连串事件") is None
    assert detect_heading("第一次见到他是在码头") is None


@pytest.mark.parametrize(("text", "expected"), [
    ("一", 1), ("十", 10), ("十二", 12), ("二十一", 21), ("零", 0),
    ("一百零五", 105), ("两千", 2000), ("007", 7),
])
def test_parses_chinese_and_arabic_ordinals(text, expected):
    assert parse_chinese_number(text) == expected


def test_rejects_unknown_ordinal_text():
    assert parse_chinese_number("甲乙") is None
    assert parse_chinese_number("") is None
