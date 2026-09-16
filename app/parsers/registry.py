from __future__ import annotations

from pathlib import Path

from .base import (
    ParseResult,
    ParserFunction,
    SourceNotFoundError,
    UnsupportedFormatError,
    hash_file,
)
from .docx import parse_docx
from .epub import parse_epub
from .markdown import parse_markdown
from .txt import parse_txt

PARSERS: dict[str, ParserFunction] = {
    ".txt": parse_txt,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".docx": parse_docx,
    ".epub": parse_epub,
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(PARSERS))


def parse_source(path: str | Path) -> ParseResult:
    """Parse a supported source file read-only and return its chapters."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise SourceNotFoundError(f"source file not found: {source}")
    parser = PARSERS.get(source.suffix.lower())
    if parser is None:
        raise UnsupportedFormatError(source.suffix, SUPPORTED_EXTENSIONS)
    return parser(source, hash_file(source))
