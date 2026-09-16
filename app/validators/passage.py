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
    for paragraph in passage.paragraphs:
        unsupported = [
            marker
            for marker in _specific_markers(paragraph.text)
            if marker.casefold() not in normalized_source
        ]
        if unsupported:
            report.add(
                "unsupported_specific_fact",
                "Specific facts do not appear in the source: " + ", ".join(sorted(set(unsupported))),
                affected_ids=[paragraph.label],
            )

    metrics = _difficulty_metrics(words, full_text)
    return report.build(
        source_coverage=(len(required_ids & covered_ids) / len(required_ids) if required_ids else 1.0),
        passage_word_count=word_count,
        paragraph_count=paragraph_count,
        metrics=metrics,
    )


def _specific_markers(text: str) -> list[str]:
    patterns = (YEAR_RE, PERCENT_RE, LARGE_NUMBER_RE, QUOTED_RE, ORG_RE, STUDY_RE, PERSON_RE)
    markers: list[str] = []
    for pattern in patterns:
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
