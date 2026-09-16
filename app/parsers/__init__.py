"""Read-only source parsers for TXT, DOCX, EPUB and Markdown corpora."""

from .base import (
    CONFIDENCE_THRESHOLD,
    PARSER_VERSION,
    ParserError,
    ParseResult,
    ParserFunction,
    SourceNotFoundError,
    UnsupportedEncodingError,
    UnsupportedFormatError,
)
from .chapter_detection import (
    ChapterDraft,
    DetectedHeading,
    HeadingKind,
    detect_heading,
)
from .registry import PARSERS, SUPPORTED_EXTENSIONS, parse_source

__all__ = [
    "CONFIDENCE_THRESHOLD",
    "PARSERS",
    "PARSER_VERSION",
    "SUPPORTED_EXTENSIONS",
    "ChapterDraft",
    "DetectedHeading",
    "HeadingKind",
    "ParseResult",
    "ParserError",
    "ParserFunction",
    "SourceNotFoundError",
    "UnsupportedEncodingError",
    "UnsupportedFormatError",
    "detect_heading",
    "parse_source",
]
