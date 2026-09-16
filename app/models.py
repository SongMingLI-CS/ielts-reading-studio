from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Difficulty(StrEnum):
    FOUNDATION = "foundation"
    STANDARD = "standard"
    ADVANCED = "advanced"


class QuestionType(StrEnum):
    MATCHING_HEADINGS = "matching_headings"
    TRUE_FALSE_NOT_GIVEN = "true_false_not_given"
    YES_NO_NOT_GIVEN = "yes_no_not_given"
    MATCHING_INFORMATION = "matching_information"
    MULTIPLE_CHOICE = "multiple_choice"
    SENTENCE_COMPLETION = "sentence_completion"
    SUMMARY_COMPLETION = "summary_completion"
    SHORT_ANSWER = "short_answer"


class UnitStatus(StrEnum):
    INDEXED = "indexed"
    AUTHOR_GENERATING = "author_generating"
    PASSAGE_REVIEWING = "passage_reviewing"
    EXAMINER_GENERATING = "examiner_generating"
    VALIDATING = "validating"
    COMPLETED = "completed"
    AUTHOR_REVISION_REQUIRED = "author_revision_required"
    EXAMINER_REVISION_REQUIRED = "examiner_revision_required"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class SourceParagraph(BaseModel):
    id: str
    label: str | None = None
    text: str
    source_offsets: dict[str, int] | None = None


class Corpus(BaseModel):
    id: str
    name: str
    source_path: str
    source_hash: str
    format: str
    encoding: str | None = None
    chapter_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    parser_version: str


class SourceChapter(BaseModel):
    id: str
    ordinal: int
    volume_title: str | None = None
    chapter_title: str | None = None
    paragraphs: list[SourceParagraph] = Field(default_factory=list)
    character_count: int | None = None
    source_offsets: dict[str, int] | None = None

    @model_validator(mode="after")
    def unique_labels(self) -> SourceChapter:
        labels = [p.label for p in self.paragraphs if p.label is not None]
        if len(labels) != len(set(labels)):
            raise ValueError("paragraph labels must be unique")
        return self


class SourceSpan(BaseModel):
    """An exact character range inside one source chapter."""

    chapter_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    character_count: int = Field(ge=0)


class GenerationUnit(BaseModel):
    id: str
    corpus_id: str
    source_chapter_ids: list[str]
    source_text_hash: str
    difficulty: Difficulty
    question_types: list[QuestionType]
    config_snapshot: dict[str, Any]
    prompt_version: str
    status: UnitStatus
    limited_source: bool = False
    ordinal: int = Field(0, ge=0)
    source_character_count: int = Field(0, ge=0)
    source_spans: list[SourceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def three_distinct_question_types(self) -> GenerationUnit:
        if len(self.question_types) != 3 or len(set(self.question_types)) != 3:
            raise ValueError("exactly three distinct question types are required")
        return self


class BriefItem(BaseModel):
    id: str
    text: str
    source_ids: list[str] = Field(default_factory=list)


class SourceBrief(BaseModel):
    core_facts: list[BriefItem] = Field(default_factory=list)
    core_claims: list[BriefItem] = Field(default_factory=list)
    causal_links: list[BriefItem] = Field(default_factory=list)
    uncertainties: list[BriefItem] = Field(default_factory=list)
    prohibited_inventions: list[str] = Field(default_factory=list)
    suggested_structure: list[str] = Field(default_factory=list)


class PassageParagraph(BaseModel):
    label: str
    text: str
    source_ids: list[str] = Field(default_factory=list)


class VocabularyEntry(BaseModel):
    word: str
    pronunciation: str | None = None
    part_of_speech: str | None = None
    chinese_meaning: str | None = None
    collocations: list[str] = Field(default_factory=list)
    example: str | None = None


class ReadingPassage(BaseModel):
    title: str
    difficulty: Difficulty
    word_count: int
    paragraphs: list[PassageParagraph]
    vocabulary: list[VocabularyEntry] = Field(default_factory=list)
    source_coverage: dict[str, Any] = Field(default_factory=dict)
    author_revision: int = 0

    @model_validator(mode="after")
    def unique_labels(self) -> ReadingPassage:
        labels = [p.label for p in self.paragraphs]
        if len(labels) != len(set(labels)):
            raise ValueError("paragraph labels must be unique")
        return self


class Question(BaseModel):
    number: int
    prompt: str
    answer: str
    acceptable_answers: list[str] = Field(default_factory=list)
    evidence_paragraph: str
    evidence_quote: str
    chinese_explanation: str
    distractor_explanations: dict[str, str] = Field(default_factory=dict)


class QuestionGroup(BaseModel):
    type: QuestionType
    instructions: str
    word_limit: int | None = None
    options: list[str] = Field(default_factory=list)
    questions: list[Question]


class UsageRecord(BaseModel):
    stage: str
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    retries: int = 0
    error_type: str | None = None


class QualityIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    stage: str = "validation"
    affected_ids: list[str] = Field(default_factory=list)
    question_number: int | None = None


class QualityReport(BaseModel):
    passed: bool = False
    issues: list[QualityIssue] = Field(default_factory=list)
    source_coverage: float | None = None
    passage_word_count: int | None = None
    paragraph_count: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    revision_count: int = 0

    @property
    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


class ReadingPackage(BaseModel):
    unit: GenerationUnit
    source_brief: SourceBrief
    passage: ReadingPassage
    question_groups: list[QuestionGroup]
    quality_report: QualityReport
    usage_records: list[UsageRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def contiguous_question_numbers(self) -> ReadingPackage:
        numbers = [q.number for group in self.question_groups for q in group.questions]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("question numbers must be contiguous starting at 1")
        return self


class ChapterSummary(BaseModel):
    """Boundary metadata kept in the corpus manifest instead of full chapter text."""

    id: str
    ordinal: int
    title: str | None = None
    volume_title: str | None = None
    character_count: int = 0
    paragraph_count: int = 0


class CorpusManifest(BaseModel):
    """Offline index of one imported corpus: chapter boundaries and generation units."""

    corpus: Corpus
    created_at: datetime = Field(default_factory=datetime.now)
    confidence: float = 0.0
    diagnostics: list[str] = Field(default_factory=list)
    total_characters: int = 0
    chapter_count: int = 0
    unit_count: int = 0
    chapters: list[ChapterSummary] = Field(default_factory=list)
    candidate_chapters: list[ChapterSummary] = Field(default_factory=list)
    units: list[GenerationUnit] = Field(default_factory=list)
