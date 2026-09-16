from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from app.models import SourceChapter

PARSER_VERSION = "1"

# Confidence below this threshold means the parse is not safe to generate from.
CONFIDENCE_THRESHOLD = 0.6

# Fixed namespace so chapters keep the same identity across re-imports.
CHAPTER_NAMESPACE = UUID("2f5c9d78-8c1e-4a5f-9c0f-2b7d1a6e4f30")

HASH_CHUNK_BYTES = 1024 * 1024


class ParserError(Exception):
    """Base class for source parsing failures."""


class SourceNotFoundError(ParserError):
    """Raised when the requested source file does not exist."""


class UnsupportedFormatError(ParserError):
    """Raised when no parser is registered for the source extension."""

    def __init__(self, suffix: str, supported: Iterable[str]):
        supported_list = ", ".join(sorted(supported))
        super().__init__(f"unsupported source format '{suffix or '(none)'}'; expected one of {supported_list}")


class UnsupportedEncodingError(ParserError):
    """Raised when a text source cannot be decoded as UTF-8 or GB18030."""


def hash_file(path: Path, chunk_size: int = HASH_CHUNK_BYTES) -> str:
    """Return the SHA-256 of the source bytes without loading the whole file."""
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chapter_identifier(corpus_hash: str, ordinal: int) -> str:
    """Return a stable chapter ID derived from the corpus hash and ordinal."""
    return str(uuid5(CHAPTER_NAMESPACE, f"{corpus_hash}:chapter:{ordinal}"))


def paragraph_identifier(chapter_id: str, index: int) -> str:
    return f"{chapter_id}-p{index}"


@dataclass(frozen=True)
class ParseResult:
    """Outcome of one source parse, including confidence and diagnostics."""

    format: str
    encoding: str | None
    source_hash: str
    chapters: list[SourceChapter]
    confidence: float
    diagnostics: list[str]
    candidate_chapters: list[SourceChapter] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


ParserFunction = Callable[[Path, str], ParseResult]
