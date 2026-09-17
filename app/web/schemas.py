from __future__ import annotations

from pydantic import BaseModel, Field


class PracticeSubmission(BaseModel):
    attempt_id: str | None = Field(None, min_length=1, max_length=100)
    answers: dict[str, str | list[str]] = Field(default_factory=dict)
    elapsed_seconds: int | None = Field(None, ge=0)


class PracticeSaveResult(BaseModel):
    attempt_id: str
    saved: bool = True


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
