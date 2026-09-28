from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

Topic = Literal["python", "numpy", "ai"]


class SourceBlock(BaseModel):
    kind: Literal["text", "code", "heading", "list", "parameter", "table"]
    text: str = ""
    level: int = 0
    language: str = ""
    rows: list[list[str]] = Field(default_factory=list)


class StudySection(BaseModel):
    id: str
    title: str
    blocks: list[SourceBlock]
    source_url: str = ""
    chapter_title: str = ""

    @property
    def text(self) -> str:
        return "\n\n".join(
            b.text if b.kind != "table" else "\n".join("\t".join(row) for row in b.rows)
            for b in self.blocks
        )


class StudySourcePage(BaseModel):
    title: str
    url: str
    version: str


class StudyDocument(BaseModel):
    id: str
    title: str
    topic: Topic
    version: str
    source_url: str
    official: bool = False
    content_hash: str
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sections: list[StudySection]
    source_pages: list[StudySourcePage] = Field(default_factory=list)

    @computed_field
    @property
    def content_characters(self) -> int:
        return sum(len(section.text) for section in self.sections)

    @computed_field
    @property
    def page_count(self) -> int:
        return len(self.source_pages) or 1

    @computed_field
    @property
    def section_count(self) -> int:
        return len(self.sections)


class StudyDocumentSummary(BaseModel):
    id: str
    title: str
    topic: Topic
    version: str
    official: bool
    section_count: int
    page_count: int
    content_characters: int | None = None


class StudyWord(BaseModel):
    term: str = Field(min_length=1, max_length=160)
    chinese: str = Field(min_length=1, max_length=400)
    english_explanation: str = Field(min_length=1, max_length=800)


class StudyConcept(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=1800)


class StudyQuestion(BaseModel):
    kind: Literal["english", "concept"]
    prompt: str = Field(min_length=1, max_length=1000)
    choices: list[str] = Field(min_length=2, max_length=5)
    correct_index: int = Field(ge=0)
    explanation: str = Field(min_length=1, max_length=1800)
    evidence_quote: str = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def valid_choice(self):
        if self.correct_index >= len(self.choices) or any(
            len(c) > 1000 for c in self.choices
        ):
            raise ValueError("invalid choices")
        return self


class StudyGuide(BaseModel):
    glossary: list[StudyWord] = Field(min_length=1, max_length=12)
    concepts: list[StudyConcept] = Field(min_length=1, max_length=8)
    questions: list[StudyQuestion] = Field(min_length=2, max_length=8)


class ReadingWord(BaseModel):
    term: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z][A-Za-z -]*$")
    chinese: str = Field(min_length=1, max_length=160)
    usage_note: str = Field(min_length=1, max_length=600)
    source_quote: str = Field(min_length=1, max_length=1000)


class ReadingGlossary(BaseModel):
    words: list[ReadingWord] = Field(min_length=1, max_length=20)
