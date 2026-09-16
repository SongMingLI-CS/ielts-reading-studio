from __future__ import annotations

import codecs
from collections.abc import Iterator
from pathlib import Path

from .base import ParseResult, UnsupportedEncodingError
from .chapter_detection import (
    ChapterDraft,
    DetectedHeading,
    detect_heading,
    finalize_chapters,
    resolve_units,
)

ENCODING_SAMPLE_BYTES = 64 * 1024
TRUNCATED_SAMPLE_TRIM = 4


def detect_text_encoding(path: Path) -> str:
    """Probe a text source as BOM, then UTF-8, then GB18030."""
    with path.open("rb") as handle:
        sample = handle.read(ENCODING_SAMPLE_BYTES)
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    for encoding in ("utf-8", "gb18030"):
        if _decodes(sample, encoding):
            return encoding
    raise UnsupportedEncodingError(f"cannot decode {path.name} as UTF-8 or GB18030")


def _decodes(sample: bytes, encoding: str) -> bool:
    # The probe may cut a multi-byte character in half, so retry a few trimmed
    # tails before declaring the sample undecodable.
    for trim in range(TRUNCATED_SAMPLE_TRIM + 1):
        candidate = sample[: len(sample) - trim] if trim else sample
        try:
            candidate.decode(encoding)
        except UnicodeDecodeError:
            continue
        return True
    return False


def iter_text_lines(path: Path, encoding: str) -> Iterator[tuple[int, str]]:
    """Yield ``(character offset, line)`` pairs while streaming the file once."""
    offset = 0
    with path.open("r", encoding=encoding, newline="") as handle:
        for raw in handle:
            yield offset, raw.rstrip("\r\n")
            offset += len(raw)


def parse_txt(path: Path, source_hash: str) -> ParseResult:
    """Parse a text source into chapters using title detection only."""
    encoding = detect_text_encoding(path)
    boundaries, total_chars = _scan(path, encoding)
    units = _collect(path, encoding, boundaries, total_chars)
    chapters, preamble_chars = resolve_units(units)
    finalized = finalize_chapters(
        source_hash, chapters, explicit_structure=False, preamble_chars=preamble_chars,
    )
    return ParseResult(
        format="txt",
        encoding=encoding,
        source_hash=source_hash,
        chapters=finalized.chapters,
        confidence=finalized.confidence,
        diagnostics=finalized.diagnostics,
        candidate_chapters=finalized.candidates,
    )


def _scan(path: Path, encoding: str) -> tuple[list[tuple[int, DetectedHeading]], int]:
    boundaries: list[tuple[int, DetectedHeading]] = []
    total_chars = 0
    for offset, line in iter_text_lines(path, encoding):
        total_chars = offset + len(line)
        heading = detect_heading(line)
        if heading is not None:
            boundaries.append((offset, heading))
    return boundaries, total_chars


def _collect(
    path: Path,
    encoding: str,
    boundaries: list[tuple[int, DetectedHeading]],
    total_chars: int,
) -> list[ChapterDraft]:
    lookup = dict(boundaries)
    units: list[ChapterDraft] = []
    current = ChapterDraft(heading=None, offsets={"start": 0, "end": 0})
    current_start = 0
    for offset, line in iter_text_lines(path, encoding):
        heading = lookup.get(offset)
        if heading is not None:
            current.offsets = {"start": current_start, "end": offset}
            units.append(current)
            current = ChapterDraft(heading=heading)
            current_start = offset
            continue
        if line.strip():
            current.paragraphs.append(line)
    current.offsets = {"start": current_start, "end": total_chars}
    units.append(current)
    return units
