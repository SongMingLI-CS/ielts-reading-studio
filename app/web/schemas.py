from __future__ import annotations

from pydantic import BaseModel, Field


class PracticeSubmission(BaseModel):
    answers: dict[str, str | list[str]] = Field(default_factory=dict)
    elapsed_seconds: int | None = Field(None, ge=0)


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
    correct: int
    total: int
    elapsed_seconds: int | None = None
    results: list[AnswerResult]
