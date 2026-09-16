from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub

from .base import ParseResult
from .chapter_detection import (
    ChapterDraft,
    DetectedHeading,
    HeadingKind,
    detect_heading,
    finalize_chapters,
    resolve_units,
)

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
BLOCK_TAGS = (*HEADING_TAGS, "p")


def parse_epub(path: Path, source_hash: str) -> ParseResult:
    """Parse an EPUB in spine order, splitting documents on internal headings."""
    book = epub.read_epub(str(path))
    units: list[ChapterDraft] = []
    for item in spine_documents(book):
        units.extend(document_units(item))
    chapters, preamble_chars = resolve_units(units)
    finalized = finalize_chapters(
        source_hash, chapters, explicit_structure=bool(units), preamble_chars=preamble_chars,
    )
    return ParseResult(
        format="epub",
        encoding="utf-8",
        source_hash=source_hash,
        chapters=finalized.chapters,
        confidence=finalized.confidence,
        diagnostics=finalized.diagnostics,
        candidate_chapters=finalized.candidates,
    )


def spine_documents(book) -> list:
    """Return content documents in spine order, skipping navigation documents."""
    documents = []
    seen_ids: set[str] = set()
    for entry in book.spine:
        item = _resolve(book, entry)
        if item is None or getattr(item, "get_type", lambda: None)() != ITEM_DOCUMENT:
            continue
        properties = getattr(item, "properties", None) or []
        name = (item.get_name() or "").lower()
        if "nav" in properties or name.endswith(("nav.xhtml", "toc.xhtml")):
            continue
        identifier = item.get_id()
        if identifier in seen_ids:
            continue
        seen_ids.add(identifier)
        documents.append(item)
    return documents


def _resolve(book, entry):
    if hasattr(entry, "get_name"):
        return entry
    # EbookLib reports spine entries as either ids or (idref, linear) tuples.
    if isinstance(entry, (tuple, list)):
        entry = entry[0]
    return book.get_item_with_id(entry)


def document_units(item) -> list[ChapterDraft]:
    """Split one XHTML document into candidate chapters on its headings."""
    soup = BeautifulSoup(item.get_content(), "html.parser")
    body = soup.body or soup
    blocks = [
        (tag.get_text(" ", strip=True), tag.name in HEADING_TAGS)
        for tag in body.find_all(BLOCK_TAGS)
        if tag.get_text(" ", strip=True)
    ]
    if not blocks:
        return []

    headings = [text for text, is_heading in blocks if is_heading]
    if len(headings) < 2:
        title = headings[0] if headings else document_title(soup, item)
        paragraphs = [text for text, is_heading in blocks if not is_heading]
        return [ChapterDraft(heading=_heading(title), paragraphs=paragraphs)]

    units: list[ChapterDraft] = []
    current: ChapterDraft | None = None
    for text, is_heading in blocks:
        if is_heading:
            if current is not None:
                units.append(current)
            current = ChapterDraft(heading=_heading(text))
            continue
        if current is None:
            current = ChapterDraft(heading=_heading(document_title(soup, item)))
        current.paragraphs.append(text)
    if current is not None:
        units.append(current)
    return units


def document_title(soup, item) -> str:
    if soup.title is not None:
        text = soup.title.get_text(" ", strip=True)
        if text:
            return text
    return item.get_name() or "untitled"


def _heading(text: str) -> DetectedHeading:
    normalized = text.strip()
    detected = detect_heading(normalized)
    if detected is not None:
        return detected
    return DetectedHeading(HeadingKind.CHAPTER, None, normalized)
