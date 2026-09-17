from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CefrLevel = Literal["B1", "B2", "C1"]

_CEFR_FALLBACK = {"A1": "B1", "A2": "B1", "C2": "C1"}


class Paragraph(BaseModel):
    id: str
    text: str


class Chapter(BaseModel):
    chapter_id: int
    chapter_title: str
    paragraphs: list[Paragraph]
    source_path: Path | None = None
    source_hash: str | None = None


class InsertedTerm(BaseModel):
    """A single English learning item.

    Fields the model tends to omit are optional so a partially specified item never invalidates a
    whole chapter response; unknown CEFR labels are folded into the B1/B2/C1 scale instead.
    """

    word: str
    lemma: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    cefr: CefrLevel = "B2"
    phonetic: str = ""
    category: str = ""
    collocation: str = ""
    example_sentence: str = ""

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not str(data.get("lemma") or "").strip():
            data["lemma"] = str(data.get("word") or "").strip().lower()
        level = str(data.get("cefr") or "").strip().upper()
        if level not in ("B1", "B2", "C1"):
            data["cefr"] = _CEFR_FALLBACK.get(level, "B2")
        return data


class PlannedReplacement(BaseModel):
    """Model's own replacement plan for one paragraph, written before the converted text."""

    zh: str
    en: str


class ConvertedParagraph(BaseModel):
    id: str
    converted_text: str
    plan: list[PlannedReplacement] = Field(default_factory=list)
    inserted_terms: list[InsertedTerm] = Field(default_factory=list)


class ConvertedChapter(BaseModel):
    chapter_id: int
    chapter_title: str
    paragraphs: list[ConvertedParagraph]


class VocabularyItem(InsertedTerm):
    first_chapter: int | None = None
    last_chapter: int | None = None
    occurrence_count: int = 0
    review_schedule: list[int] = Field(default_factory=list)


class UsageRecord(BaseModel):
    chapter_id: int
    model: str
    request_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input_tokens: int = 0
    output_tokens: int = 0
    retry_count: int = 0
    status: str
    error_type: str | None = None

