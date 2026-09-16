from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from app.models import SourceChapter

BOUNDARY_NAMESPACE = UUID("49d31264-0715-4f87-a136-c72168d66cf8")


class BoundaryEdit(BaseModel):
    action: Literal["rename", "merge_next", "split_before_paragraph"]
    chapter_id: str
    paragraph_index: int | None = Field(None, ge=1)
    title: str | None = None


def apply_boundary_edits(
    chapters: list[SourceChapter],
    edits: list[BoundaryEdit],
) -> list[SourceChapter]:
    """Apply explicit, auditable boundary edits without touching source files."""
    result = [chapter.model_copy(deep=True) for chapter in chapters]
    for edit in edits:
        index = next((i for i, chapter in enumerate(result) if chapter.id == edit.chapter_id), None)
        if index is None:
            raise ValueError(f"Unknown chapter_id: {edit.chapter_id}")
        chapter = result[index]
        if edit.action == "rename":
            if not edit.title or not edit.title.strip():
                raise ValueError("rename requires a non-empty title")
            result[index] = chapter.model_copy(update={"chapter_title": edit.title.strip()})
        elif edit.action == "merge_next":
            if index + 1 >= len(result):
                raise ValueError("merge_next requires a following chapter")
            following = result[index + 1]
            title = edit.title.strip() if edit.title and edit.title.strip() else chapter.chapter_title
            merged = SourceChapter(
                id=_derived_id("merge", chapter.id, following.id),
                ordinal=chapter.ordinal,
                volume_title=chapter.volume_title or following.volume_title,
                chapter_title=title,
                paragraphs=[*chapter.paragraphs, *following.paragraphs],
                character_count=_character_count([*chapter.paragraphs, *following.paragraphs]),
                source_offsets=_combined_offsets(chapter, following),
            )
            result[index : index + 2] = [merged]
        else:
            split = edit.paragraph_index
            if split is None or split >= len(chapter.paragraphs):
                raise ValueError("split_before_paragraph must be inside the chapter")
            first_paragraphs = chapter.paragraphs[:split]
            second_paragraphs = chapter.paragraphs[split:]
            second_title = (
                edit.title.strip()
                if edit.title and edit.title.strip()
                else f"{chapter.chapter_title or 'Chapter'} (part 2)"
            )
            first = chapter.model_copy(
                update={
                    "id": _derived_id("split-first", chapter.id, str(split)),
                    "paragraphs": first_paragraphs,
                    "character_count": _character_count(first_paragraphs),
                }
            )
            second = chapter.model_copy(
                update={
                    "id": _derived_id("split-second", chapter.id, str(split)),
                    "chapter_title": second_title,
                    "paragraphs": second_paragraphs,
                    "character_count": _character_count(second_paragraphs),
                }
            )
            result[index : index + 1] = [first, second]
    return [chapter.model_copy(update={"ordinal": index}) for index, chapter in enumerate(result, start=1)]


def _derived_id(*parts: str) -> str:
    return str(uuid5(BOUNDARY_NAMESPACE, "|".join(parts)))


def _character_count(paragraphs) -> int:
    return sum(len(paragraph.text) for paragraph in paragraphs)


def _combined_offsets(first: SourceChapter, second: SourceChapter) -> dict[str, int] | None:
    offsets = [value for value in (first.source_offsets, second.source_offsets) if value]
    starts = [value["start"] for value in offsets if "start" in value]
    ends = [value["end"] for value in offsets if "end" in value]
    if not starts or not ends:
        return None
    return {"start": min(starts), "end": max(ends)}
