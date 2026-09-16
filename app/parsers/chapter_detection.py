from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from statistics import median

from app.models import SourceChapter, SourceParagraph

from .base import CONFIDENCE_THRESHOLD, chapter_identifier, paragraph_identifier

# A heading is a short standalone line. Longer lines are prose that happens to
# mention a chapter, so they must never become a boundary.
MAX_HEADING_LENGTH = 40

# Sentence punctuation inside a candidate line proves it is prose, not a title.
SENTENCE_MARKS = "。！？；!?;，,"

CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_NUMBER_CHARS = "".join(sorted(set(CN_DIGITS) | set(CN_UNITS)))

NUMBER = rf"(?:\d{{1,5}}|[{CN_NUMBER_CHARS}]+)"

CHAPTER_PATTERN = re.compile(rf"^第\s*(?P<num>{NUMBER})\s*(?P<unit>[章回节])(?P<rest>.*)$")
VOLUME_PATTERN = re.compile(
    rf"^(?:第\s*(?P<n1>{NUMBER})\s*(?P<u1>[卷部篇])|(?P<u2>[卷部篇])\s*(?P<n2>{NUMBER}))(?P<rest>.*)$"
)
ENGLISH_PATTERN = re.compile(
    r"^(?P<word>chapter|part|unit)\s+(?P<num>\d{1,4})\b(?P<rest>.*)$", re.IGNORECASE
)
PREFACE_PATTERN = re.compile(r"^(?P<word>序章|序言|前言|楔子|自序|引言|序)(?P<rest>.*)$")
EPILOGUE_PATTERN = re.compile(r"^(?P<word>终章|尾声|后记|结语|结尾)(?P<rest>.*)$")
EXTRA_PATTERN = re.compile(r"^(?P<word>番外|外传|后传)(?P<rest>.*)$")

WHITESPACE_PATTERN = re.compile(r"\s+")


class HeadingKind(StrEnum):
    CHAPTER = "chapter"
    VOLUME = "volume"
    PREFACE = "preface"
    EPILOGUE = "epilogue"
    EXTRA = "extra"


@dataclass(frozen=True)
class DetectedHeading:
    """One recognized structural line, with its stated ordinal when present."""

    kind: HeadingKind
    ordinal: int | None
    title: str

    @property
    def structural(self) -> bool:
        """Volume markers describe the surrounding text instead of starting a chapter."""
        return self.kind is not HeadingKind.VOLUME


def normalize_heading_text(line: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", line).strip()


def parse_chinese_number(text: str) -> int | None:
    """Convert an Arabic or Chinese numeral such as ``001``, ``十二`` or ``一百零五``."""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    current = 0
    seen = False
    for character in text:
        if character in CN_DIGITS:
            current = CN_DIGITS[character]
            seen = True
        elif character in CN_UNITS:
            total += (current or 1) * CN_UNITS[character]
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + current


def detect_heading(line: str) -> DetectedHeading | None:
    """Recognize a supported chapter, volume, preface, epilogue or extra title."""
    text = normalize_heading_text(line)
    if not text or len(text) > MAX_HEADING_LENGTH:
        return None
    if any(mark in text for mark in SENTENCE_MARKS):
        return None

    match = VOLUME_PATTERN.match(text)
    if match is not None:
        ordinal = parse_chinese_number(match["n1"] or match["n2"] or "")
        return DetectedHeading(HeadingKind.VOLUME, ordinal, text)

    match = CHAPTER_PATTERN.match(text)
    if match is not None:
        return DetectedHeading(HeadingKind.CHAPTER, parse_chinese_number(match["num"]), text)

    match = ENGLISH_PATTERN.match(text)
    if match is not None:
        return DetectedHeading(HeadingKind.CHAPTER, int(match["num"]), text)

    for pattern, kind in (
        (PREFACE_PATTERN, HeadingKind.PREFACE),
        (EPILOGUE_PATTERN, HeadingKind.EPILOGUE),
        (EXTRA_PATTERN, HeadingKind.EXTRA),
    ):
        if pattern.match(text) is not None:
            return DetectedHeading(kind, None, text)
    return None


@dataclass
class ChapterDraft:
    """A candidate chapter before confidence scoring and identifier assignment."""

    heading: DetectedHeading | None
    paragraphs: list[str] = field(default_factory=list)
    volume_title: str | None = None
    offsets: dict[str, int] | None = None

    @property
    def title(self) -> str | None:
        return self.heading.title if self.heading is not None else None

    @property
    def character_count(self) -> int:
        return sum(len(paragraph) for paragraph in self.paragraphs)


@dataclass(frozen=True)
class FinalizedParse:
    chapters: list[SourceChapter]
    candidates: list[SourceChapter]
    confidence: float
    diagnostics: list[str]


PREAMBLE_RATIO_LIMIT = 0.2
MIN_TARGET_CHAPTER_CHARS = 800
MAX_TARGET_CHAPTER_CHARS = 20_000

STRUCTURE_WEIGHT = 0.35
CONTINUITY_WEIGHT = 0.25
PREAMBLE_WEIGHT = 0.15
EMPTY_WEIGHT = 0.10
LENGTH_WEIGHT = 0.15


def resolve_units(units: list[ChapterDraft]) -> tuple[list[ChapterDraft], int]:
    """Drop volume markers and leading preamble text, keeping chapter order.

    Returns the remaining chapter drafts plus the number of preamble characters.
    """
    chapters: list[ChapterDraft] = []
    preamble_chars = 0
    volume: str | None = None
    for unit in units:
        if unit.heading is None:
            preamble_chars += unit.character_count
            continue
        if unit.heading.kind is HeadingKind.VOLUME:
            volume = unit.heading.title
            continue
        unit.volume_title = volume
        chapters.append(unit)
    return chapters, preamble_chars


def finalize_chapters(
    corpus_hash: str,
    drafts: list[ChapterDraft],
    *,
    explicit_structure: bool,
    preamble_chars: int = 0,
) -> FinalizedParse:
    """Score a chapter split and keep it only when the boundaries look reliable."""
    candidates = [
        build_chapter(corpus_hash, ordinal, draft) for ordinal, draft in enumerate(drafts, start=1)
    ]
    usable = [chapter for chapter in candidates if chapter.character_count]
    # A text file needs at least two usable chapters; a document with native
    # structure such as Word heading styles is trusted with a single chapter.
    required = 1 if explicit_structure else 2

    diagnostics: list[str] = []
    if len(usable) < required:
        diagnostics.append("no_reliable_boundaries")
        diagnostics.append(f"usable_chapters:{len(usable)}")
        return FinalizedParse(chapters=[], candidates=candidates, confidence=0.0, diagnostics=diagnostics)

    empty_count = len(candidates) - len(usable)
    if empty_count:
        diagnostics.append(f"empty_chapters:{empty_count}")

    continuity = continuity_score(drafts)
    if continuity < 1.0:
        diagnostics.append("ordinal_gap")

    total_chars = preamble_chars + sum(chapter.character_count or 0 for chapter in candidates)
    preamble_ratio = preamble_chars / total_chars if total_chars else 0.0
    if preamble_ratio > PREAMBLE_RATIO_LIMIT:
        diagnostics.append(f"large_preamble:{preamble_ratio:.2f}")

    lengths = [chapter.character_count for chapter in candidates if chapter.character_count]
    median_length = float(median(lengths)) if lengths else 0.0
    structure = 1.0 if explicit_structure else min(len(usable) / 3.0, 1.0)

    confidence = (
        STRUCTURE_WEIGHT * structure
        + CONTINUITY_WEIGHT * continuity
        + PREAMBLE_WEIGHT * preamble_score(preamble_ratio)
        + EMPTY_WEIGHT * (1.0 - empty_count / max(len(candidates), 1))
        + LENGTH_WEIGHT * length_score(median_length)
    )
    confidence = round(min(max(confidence, 0.0), 1.0), 4)

    if confidence < CONFIDENCE_THRESHOLD:
        diagnostics.append("low_confidence_structure")
        return FinalizedParse(chapters=[], candidates=candidates, confidence=confidence, diagnostics=diagnostics)
    return FinalizedParse(chapters=candidates, candidates=candidates, confidence=confidence, diagnostics=diagnostics)


def build_chapter(corpus_hash: str, ordinal: int, draft: ChapterDraft) -> SourceChapter:
    identifier = chapter_identifier(corpus_hash, ordinal)
    paragraphs = [
        SourceParagraph(id=paragraph_identifier(identifier, position), text=text)
        for position, text in enumerate(draft.paragraphs, start=1)
    ]
    return SourceChapter(
        id=identifier,
        ordinal=ordinal,
        volume_title=draft.volume_title,
        chapter_title=draft.title,
        paragraphs=paragraphs,
        character_count=sum(len(paragraph.text) for paragraph in paragraphs),
        source_offsets=draft.offsets,
    )


def continuity_score(drafts: list[ChapterDraft]) -> float:
    """Fraction of adjacent numbered chapters that keep increasing."""
    ordinals = [
        draft.heading.ordinal
        for draft in drafts
        if draft.heading is not None
        and draft.heading.kind is HeadingKind.CHAPTER
        and draft.heading.ordinal is not None
    ]
    if len(ordinals) < 2:
        return 1.0
    increasing = sum(1 for left, right in pairwise(ordinals) if right > left)
    return increasing / (len(ordinals) - 1)


def preamble_score(ratio: float) -> float:
    if ratio <= PREAMBLE_RATIO_LIMIT:
        return 1.0
    return max(0.0, 1.0 - (ratio - PREAMBLE_RATIO_LIMIT) / (1.0 - PREAMBLE_RATIO_LIMIT))


def length_score(median_length: float) -> float:
    if median_length <= 0:
        return 0.0
    if median_length < MIN_TARGET_CHAPTER_CHARS:
        return median_length / MIN_TARGET_CHAPTER_CHARS
    if median_length <= MAX_TARGET_CHAPTER_CHARS:
        return 1.0
    return max(0.0, MAX_TARGET_CHAPTER_CHARS / median_length)
