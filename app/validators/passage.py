from __future__ import annotations

import re
import statistics
from collections import Counter

from app.models import ReadingPassage, SourceBrief

from .reports import ReportBuilder

WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")
YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|21\d{2})\b")
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
LARGE_NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{4,}\b")
QUOTED_RE = re.compile(r"[\"“‘']([^\"”’']{3,})[\"”’']")
ORG_RE = re.compile(
    r"\b[A-Z][A-Za-z&.-]*(?:\s+[A-Z][A-Za-z&.-]*)*\s+"
    r"(?:University|Institute|Agency|Council|Association|Foundation)\b"
)
STUDY_RE = re.compile(r"\b[A-Z][A-Za-z-]+\s+(?:study|survey|report)\b")
PERSON_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER|INSERT\s+.+?\s+HERE)\b", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3400-\u9fff]")

COMMON_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "available", "be", "because", "been",
    "but", "by", "can", "carefully", "change", "communities", "could", "decline",
    "for", "from", "had", "has", "have", "homes", "in", "into", "is", "it", "its",
    "local", "manage", "may", "more", "of", "on", "or", "seasonal", "should",
    "supply", "than", "that", "the", "their", "these", "they", "this", "to", "use",
    "water", "when", "which", "with", "would",
})
CONNECTIVES = (
    "however",
    "therefore",
    "because",
    "although",
    "consequently",
    "in contrast",
    "for example",
    "in addition",
)


def validate_passage(source_text: str, brief: SourceBrief, passage: ReadingPassage):
    report = ReportBuilder("passage")
    full_text = "\n".join(paragraph.text for paragraph in passage.paragraphs)
    words = WORD_RE.findall(full_text)
    word_count = len(words)
    paragraph_count = len(passage.paragraphs)

    if not 700 <= word_count <= 900:
        report.add(
            "word_count_out_of_range",
            f"Passage has {word_count} words; expected 700–900.",
        )
    if passage.word_count != word_count:
        report.add(
            "word_count_mismatch",
            f"Declared word count {passage.word_count} does not match {word_count}.",
        )
    if not 6 <= paragraph_count <= 9:
        report.add(
            "paragraph_count_out_of_range",
            f"Passage has {paragraph_count} paragraphs; expected 6–9.",
        )
    expected_labels = [chr(ord("A") + index) for index in range(paragraph_count)]
    actual_labels = [paragraph.label for paragraph in passage.paragraphs]
    if actual_labels != expected_labels:
        report.add(
            "paragraph_labels_invalid",
            "Paragraph labels must be unique and continuous from A.",
            affected_ids=actual_labels,
        )

    unmapped = [paragraph.label for paragraph in passage.paragraphs if not paragraph.source_ids]
    if unmapped:
        report.add(
            "paragraph_source_mapping_missing",
            "Every paragraph must cite at least one source-brief item.",
            affected_ids=unmapped,
        )
    required_ids = {
        item.id for item in [*brief.core_facts, *brief.core_claims]
    }
    covered_ids = {
        source_id
        for paragraph in passage.paragraphs
        for source_id in paragraph.source_ids
    } | set(passage.source_coverage)
    missing_ids = sorted(required_ids - covered_ids)
    if missing_ids:
        report.add(
            "source_brief_not_covered",
            "Core source-brief items are neither covered nor explicitly accounted for.",
            affected_ids=missing_ids,
        )

    if "```" in full_text:
        report.add("markdown_fence_present", "Passage contains a Markdown code fence.")
    if PLACEHOLDER_RE.search(full_text):
        report.add("unfinished_placeholder", "Passage contains an unfinished placeholder.")

    normalized_source = source_text.casefold()
    cross_language = bool(CJK_RE.search(source_text)) and bool(WORD_RE.search(full_text))
    for paragraph in passage.paragraphs:
        markers = (
            _language_invariant_markers(paragraph.text)
            if cross_language
            else _specific_markers(paragraph.text)
        )
        unsupported = [
            marker
            for marker in markers
            if marker.casefold() not in normalized_source
        ]
        if unsupported:
            report.add(
                "unsupported_specific_fact",
                "Specific facts do not appear in the source: " + ", ".join(sorted(set(unsupported))),
                affected_ids=[paragraph.label],
            )

    metrics = _difficulty_metrics(words, full_text)
    metrics["vocabulary_entries"] = len(passage.vocabulary)
    _validate_vocabulary(passage, full_text, report)
    return report.build(
        source_coverage=(len(required_ids & covered_ids) / len(required_ids) if required_ids else 1.0),
        passage_word_count=word_count,
        paragraph_count=paragraph_count,
        metrics=metrics,
    )


VOCABULARY_MIN_ENTRIES = 8


def _validate_vocabulary(
    passage: ReadingPassage,
    full_text: str,
    report: ReportBuilder,
) -> None:
    """词汇表是词汇功能的数据源，字段残缺会让整个板块失效。

    线上曾出现只给 ``word``、其余字段全为 null 的情况（模型只看到
    ``"vocabulary":[]`` 这个示例，没有字段说明），这里把它变成明确的质检项：
    条数够、词条在原文里出现过、必须有中文释义、不能是专有名词短语。
    """
    entries = passage.vocabulary
    if 0 < len(entries) < VOCABULARY_MIN_ENTRIES:
        # 空列表不拦（等于"这篇没给词汇"），但给了却给不齐是要返工的。
        report.add(
            "vocabulary_too_short",
            f"Passage lists {len(entries)} vocabulary entries; expected either none "
            f"or at least {VOCABULARY_MIN_ENTRIES}.",
        )
    missing_meaning = [
        entry.word for entry in entries if not (entry.chinese_meaning or "").strip()
    ]
    if missing_meaning:
        report.add(
            "vocabulary_meaning_missing",
            "Every vocabulary entry needs a Chinese meaning.",
            affected_ids=missing_meaning[:12],
        )
    missing_pos = [
        entry.word for entry in entries if not (entry.part_of_speech or "").strip()
    ]
    if missing_pos:
        report.add(
            "vocabulary_pos_missing",
            "Every vocabulary entry needs a part of speech.",
            affected_ids=missing_pos[:12],
        )
    haystack = full_text.casefold()
    absent = [
        entry.word
        for entry in entries
        if entry.word.strip() and entry.word.strip().casefold() not in haystack
    ]
    if absent:
        report.add(
            "vocabulary_word_not_in_passage",
            "Vocabulary entries must appear in the passage.",
            affected_ids=absent[:12],
        )
    unusable = [
        entry.word
        for entry in entries
        if len(entry.word.split()) > 3 or any(ch.isdigit() for ch in entry.word)
    ]
    if unusable:
        report.add(
            "vocabulary_entry_not_a_term",
            "Vocabulary entries must be short terms, not sentences or numbered items.",
            affected_ids=unusable[:12],
        )


def _specific_markers(text: str) -> list[str]:
    patterns = (YEAR_RE, PERCENT_RE, LARGE_NUMBER_RE, QUOTED_RE, ORG_RE, STUDY_RE, PERSON_RE)
    markers: list[str] = []
    for pattern in patterns:
        markers.extend(match.group(0) for match in pattern.finditer(text))
    return markers


def _language_invariant_markers(text: str) -> list[str]:
    """Return facts that can be compared literally across Chinese/English text."""
    markers: list[str] = []
    for pattern in (YEAR_RE, PERCENT_RE, LARGE_NUMBER_RE):
        markers.extend(match.group(0) for match in pattern.finditer(text))
    return markers


def _difficulty_metrics(words: list[str], text: str) -> dict[str, float | int]:
    lowered = [word.casefold() for word in words]
    sentences = [part for part in SENTENCE_RE.findall(text) if WORD_RE.search(part)]
    sentence_lengths = [len(WORD_RE.findall(sentence)) for sentence in sentences]
    uncommon = sum(word not in COMMON_WORDS for word in lowered)
    counts = Counter(lowered)
    return {
        "median_sentence_length": statistics.median(sentence_lengths) if sentence_lengths else 0.0,
        "uncommon_word_ratio": round(uncommon / len(lowered), 4) if lowered else 0.0,
        "type_token_ratio": round(len(counts) / len(lowered), 4) if lowered else 0.0,
        "connective_count": sum(text.casefold().count(value) for value in CONNECTIVES),
    }
