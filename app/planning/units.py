from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid5

from app.config import AppConfig
from app.models import (
    ChapterSummary,
    Corpus,
    CorpusManifest,
    Difficulty,
    GenerationUnit,
    QuestionType,
    SourceChapter,
    SourceSpan,
    UnitStatus,
)

DEFAULT_PROMPT_VERSION = "1"

# Stable namespace so a corpus always plans the same unit identities.
UNIT_NAMESPACE = UUID("8b6f2c1d-4e77-4a30-9f5b-1d2c3e4a5b60")

# The design fixes one valid type combination per difficulty; users may override it
# with any three distinct types from the pool.
DEFAULT_QUESTION_TYPES: dict[Difficulty, list[QuestionType]] = {
    Difficulty.FOUNDATION: [
        QuestionType.MATCHING_HEADINGS,
        QuestionType.TRUE_FALSE_NOT_GIVEN,
        QuestionType.SENTENCE_COMPLETION,
    ],
    Difficulty.STANDARD: [
        QuestionType.MATCHING_HEADINGS,
        QuestionType.TRUE_FALSE_NOT_GIVEN,
        QuestionType.SUMMARY_COMPLETION,
    ],
    Difficulty.ADVANCED: [
        QuestionType.MATCHING_INFORMATION,
        QuestionType.YES_NO_NOT_GIVEN,
        QuestionType.MULTIPLE_CHOICE,
    ],
}

QUESTIONS_PER_DIFFICULTY: dict[Difficulty, int] = {
    Difficulty.FOUNDATION: 10,
    Difficulty.STANDARD: 12,
    Difficulty.ADVANCED: 13,
}

SENTENCE_ENDINGS = "。！？；.!?;"

CONFIG_SNAPSHOT_FIELDS = (
    "author_model",
    "examiner_model",
    "author_revision_limit",
    "examiner_revision_limit",
    "min_source_chars",
    "max_merged_chapters",
    "max_merged_chars",
    "split_source_chars",
    "split_min_chars",
    "split_max_chars",
)


def default_question_types(difficulty: Difficulty) -> list[QuestionType]:
    return list(DEFAULT_QUESTION_TYPES[difficulty])


def question_total(difficulty: Difficulty) -> int:
    return QUESTIONS_PER_DIFFICULTY[difficulty]


def config_snapshot(config: AppConfig) -> dict[str, Any]:
    """Freeze the generation-relevant configuration without paths or secrets."""
    return {field: getattr(config, field) for field in CONFIG_SNAPSHOT_FIELDS}


def chapter_length(chapter: SourceChapter) -> int:
    """Character length of a chapter using the paragraph payload the planner slices."""
    if chapter.paragraphs:
        return sum(len(paragraph.text) for paragraph in chapter.paragraphs)
    return chapter.character_count or 0


def chapter_text(chapter: SourceChapter) -> str:
    return "".join(paragraph.text for paragraph in chapter.paragraphs)


def paragraph_offsets(chapter: SourceChapter) -> list[tuple[int, int]]:
    """Character offsets of every paragraph inside the concatenated chapter text."""
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for paragraph in chapter.paragraphs:
        end = cursor + len(paragraph.text)
        offsets.append((cursor, end))
        cursor = end
    return offsets


def span_paragraphs(chapter: SourceChapter, start: int, end: int) -> list[str]:
    """Paragraph fragments covered by ``[start, end)`` of the concatenated chapter text."""
    pieces: list[str] = []
    for (paragraph_start, paragraph_end), paragraph in zip(
        paragraph_offsets(chapter), chapter.paragraphs
    ):
        if paragraph_end <= start or paragraph_start >= end:
            continue
        local_start = max(0, start - paragraph_start)
        local_end = min(len(paragraph.text), end - paragraph_start)
        fragment = paragraph.text[local_start:local_end]
        if fragment:
            pieces.append(fragment)
    return pieces


def hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def unit_id(corpus_id: str, spans: Sequence[SourceSpan]) -> str:
    """Stable UUIDv5 built from the corpus identity plus chapter IDs and offsets."""
    signature = "|".join(
        f"{span.chapter_id}:{span.start}:{span.end}" for span in spans
    )
    return str(uuid5(UNIT_NAMESPACE, f"{corpus_id}|{signature}"))


def unit_source_text(unit: GenerationUnit, chapters: Mapping[str, SourceChapter]) -> str:
    """Rebuild the exact source text a unit was planned from."""
    pieces: list[str] = []
    for span in unit.source_spans:
        chapter = chapters[span.chapter_id]
        pieces.extend(span_paragraphs(chapter, span.start, span.end))
    return "\n".join(pieces)


def spans_text(chapters: Sequence[SourceChapter], spans: Sequence[SourceSpan]) -> str:
    by_id = {chapter.id: chapter for chapter in chapters}
    pieces: list[str] = []
    for span in spans:
        pieces.extend(span_paragraphs(by_id[span.chapter_id], span.start, span.end))
    return "\n".join(pieces)


def split_spans(chapter: SourceChapter, min_chars: int, max_chars: int) -> list[tuple[int, int]]:
    """Split a long chapter into ``min_chars``-``max_chars`` ranges on paragraph boundaries."""
    total = chapter_length(chapter)
    if total <= max_chars:
        return [(0, total)]

    offsets = paragraph_offsets(chapter)
    groups: list[list[int]] = []
    current: list[int] = []
    current_length = 0
    for index, (start, end) in enumerate(offsets):
        length = end - start
        if length > max_chars:
            if current:
                groups.append(current)
                current, current_length = [], 0
            groups.append([index])
            continue
        if current and current_length + length > max_chars:
            groups.append(current)
            current, current_length = [], 0
        current.append(index)
        current_length += length
    if current:
        groups.append(current)

    def length_of(group: list[int]) -> int:
        return sum(offsets[index][1] - offsets[index][0] for index in group)

    # Walk whole paragraphs back so the final range is not left below the minimum.
    while len(groups) > 1 and length_of(groups[-1]) < min_chars and len(groups[-2]) > 1:
        candidate = groups[-2][-1]
        candidate_length = offsets[candidate][1] - offsets[candidate][0]
        if length_of(groups[-2]) - candidate_length < min_chars:
            break
        groups[-1].insert(0, groups[-2].pop())

    spans: list[tuple[int, int]] = []
    for group in groups:
        start, end = offsets[group[0]][0], offsets[group[-1]][1]
        if end - start > max_chars:
            spans.extend(cut_oversized(chapter, start, end, max_chars))
        else:
            spans.append((start, end))
    return spans


def cut_oversized(chapter: SourceChapter, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Cut one oversized paragraph, preferring a sentence boundary before the limit."""
    text = chapter_text(chapter)
    spans: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > max_chars:
        limit = cursor + max_chars
        window = text[cursor:limit]
        cut = max((window.rfind(mark) for mark in SENTENCE_ENDINGS), default=-1)
        boundary = cursor + cut + 1 if cut >= 0 else limit
        spans.append((cursor, boundary))
        cursor = boundary
    if end > cursor:
        spans.append((cursor, end))
    return spans


def plan_units(
    chapters: list[SourceChapter],
    config: AppConfig,
    difficulty: Difficulty,
    question_types: list[QuestionType],
    *,
    corpus_id: str = "",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> list[GenerationUnit]:
    """Merge short chapters and split long ones into deterministic generation units."""
    snapshot = config_snapshot(config)
    units: list[GenerationUnit] = []

    def emit(group: Sequence[SourceChapter], spans: Sequence[SourceSpan]) -> None:
        units.append(
            build_unit(
                group,
                spans,
                corpus_id=corpus_id,
                difficulty=difficulty,
                question_types=question_types,
                snapshot=snapshot,
                prompt_version=prompt_version,
                ordinal=len(units) + 1,
                min_source_chars=config.min_source_chars,
            )
        )

    pending: list[SourceChapter] = []
    pending_chars = 0

    def flush_pending() -> None:
        nonlocal pending, pending_chars
        if pending:
            emit(
                pending,
                [
                    SourceSpan(
                        chapter_id=chapter.id,
                        start=0,
                        end=chapter_length(chapter),
                        character_count=chapter_length(chapter),
                    )
                    for chapter in pending
                ],
            )
        pending, pending_chars = [], 0

    for chapter in chapters:
        length = chapter_length(chapter)
        if length == 0:
            continue
        if length > config.split_source_chars:
            flush_pending()
            for start, end in split_spans(chapter, config.split_min_chars, config.split_max_chars):
                emit(
                    [chapter],
                    [
                        SourceSpan(
                            chapter_id=chapter.id,
                            start=start,
                            end=end,
                            character_count=end - start,
                        )
                    ],
                )
            continue
        if pending and (
            len(pending) >= config.max_merged_chapters
            or pending_chars >= config.min_source_chars
            or pending_chars + length > config.max_merged_chars
        ):
            flush_pending()
        pending.append(chapter)
        pending_chars += length
    flush_pending()
    return units


def build_unit(
    chapters: Sequence[SourceChapter],
    spans: Sequence[SourceSpan],
    *,
    corpus_id: str,
    difficulty: Difficulty,
    question_types: list[QuestionType],
    snapshot: dict[str, Any],
    prompt_version: str,
    ordinal: int,
    min_source_chars: int,
) -> GenerationUnit:
    character_count = sum(span.character_count for span in spans)
    return GenerationUnit(
        id=unit_id(corpus_id, spans),
        corpus_id=corpus_id,
        source_chapter_ids=[chapter.id for chapter in chapters],
        source_text_hash=hash_text(spans_text(chapters, spans)),
        difficulty=difficulty,
        question_types=list(question_types),
        config_snapshot=dict(snapshot),
        prompt_version=prompt_version,
        status=UnitStatus.INDEXED,
        limited_source=character_count < min_source_chars,
        ordinal=ordinal,
        source_character_count=character_count,
        source_spans=list(spans),
    )


def summarize_chapter(chapter: SourceChapter) -> ChapterSummary:
    return ChapterSummary(
        id=chapter.id,
        ordinal=chapter.ordinal,
        title=chapter.chapter_title,
        volume_title=chapter.volume_title,
        character_count=chapter_length(chapter),
        paragraph_count=len(chapter.paragraphs),
    )


def build_manifest(
    corpus: Corpus,
    chapters: list[SourceChapter],
    units: list[GenerationUnit],
    *,
    confidence: float = 0.0,
    diagnostics: list[str] | None = None,
    candidate_chapters: list[SourceChapter] | None = None,
) -> CorpusManifest:
    """Index one corpus for offline review without embedding full chapter text."""
    summaries = [summarize_chapter(chapter) for chapter in chapters]
    candidates: list[ChapterSummary] = []
    if not summaries and candidate_chapters:
        # Keep the rejected split visible so the preview page can offer boundary edits.
        candidates = [summarize_chapter(chapter) for chapter in candidate_chapters]
    return CorpusManifest(
        corpus=corpus,
        confidence=confidence,
        diagnostics=list(diagnostics or []),
        total_characters=sum(chapter_length(chapter) for chapter in chapters),
        chapter_count=len(chapters),
        unit_count=len(units),
        chapters=summaries,
        candidate_chapters=candidates,
        units=list(units),
    )
