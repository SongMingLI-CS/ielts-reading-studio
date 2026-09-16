from __future__ import annotations

import re
from pathlib import Path

from .base import ParseResult
from .chapter_detection import (
    ChapterDraft,
    DetectedHeading,
    HeadingKind,
    detect_heading,
    finalize_chapters,
    resolve_units,
)
from .txt import detect_text_encoding, iter_text_lines

HEADING_PATTERN = re.compile(r"^(?P<level>#{1,3})[ \t]+(?P<title>.+?)[ \t]*#*[ \t]*$")


def parse_markdown(path: Path, source_hash: str) -> ParseResult:
    """Parse a Markdown source using level 1-3 headings as chapter boundaries."""
    encoding = detect_text_encoding(path)
    units = _collect(path, encoding)
    chapters, preamble_chars = resolve_units(units)
    finalized = finalize_chapters(
        source_hash, chapters, explicit_structure=True, preamble_chars=preamble_chars,
    )
    return ParseResult(
        format="md",
        encoding=encoding,
        source_hash=source_hash,
        chapters=finalized.chapters,
        confidence=finalized.confidence,
        diagnostics=finalized.diagnostics,
        candidate_chapters=finalized.candidates,
    )


def _collect(path: Path, encoding: str) -> list[ChapterDraft]:
    units: list[ChapterDraft] = []
    current = ChapterDraft(heading=None, offsets={"start": 0, "end": 0})
    current_start = 0
    total_chars = 0
    for offset, line in iter_text_lines(path, encoding):
        total_chars = offset + len(line)
        match = HEADING_PATTERN.match(line)
        if match is not None:
            current.offsets = {"start": current_start, "end": offset}
            units.append(current)
            current = ChapterDraft(heading=heading_from_title(match["title"]))
            current_start = offset
            continue
        text = line.strip()
        if text:
            current.paragraphs.append(text)
    current.offsets = {"start": current_start, "end": total_chars}
    units.append(current)
    return units


def heading_from_title(title: str) -> DetectedHeading:
    """Prefer the recognized chapter/volume kind, otherwise treat the heading as a chapter."""
    normalized = title.strip()
    detected = detect_heading(normalized)
    if detected is not None:
        return detected
    return DetectedHeading(HeadingKind.CHAPTER, None, normalized)
