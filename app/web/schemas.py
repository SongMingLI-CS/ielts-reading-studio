from __future__ import annotations

from pydantic import BaseModel, Field


class ReadingUIState(BaseModel):
    current: int = Field(1, ge=1)
    flags: list[int] = Field(default_factory=list, max_length=100)
    highlights: dict[str, list[list[int]]] = Field(default_factory=dict)
    text_size: int = Field(18, ge=18, le=22)
    pane: str = Field("reading", pattern="^(reading|questions)$")
    reading_anchor: str | None = Field(None, max_length=100)
    reading_offset: float = 0
    question_anchor: str | None = Field(None, max_length=100)
    question_offset: float = 0


class PracticeSubmission(BaseModel):
    attempt_id: str | None = Field(None, min_length=1, max_length=100)
    answers: dict[str, str | list[str]] = Field(default_factory=dict)
    elapsed_seconds: int | None = Field(None, ge=0)
    revision: int | None = Field(None, ge=0)
    ui: ReadingUIState | None = None


class PracticeSaveResult(BaseModel):
    attempt_id: str
    saved: bool = True
    revision: int = 0
    status: str = "in_progress"
    mode: str = "study"
    deadline: float | None = None
    server_time: float
    redirect_url: str | None = None


class PracticeStart(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    mode: str = Field("study", pattern="^(study|exam)$")
    minutes: int = Field(20, ge=1, le=60)


class AnswerResult(BaseModel):
    number: int
    correct: bool
    submitted: str | list[str]
    answer: str
    acceptable_answers: list[str]
    evidence_paragraph: str
    evidence_quote: str
    chinese_explanation: str
    distractor_explanations: dict[str, str]


class PracticeResult(BaseModel):
    attempt_id: str
    correct: int
    total: int
    elapsed_seconds: int | None = None
    redirect_url: str | None = None
    results: list[AnswerResult]
