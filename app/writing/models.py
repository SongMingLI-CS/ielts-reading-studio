from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WritingTaskType(StrEnum):
    TASK_1 = "task_1"
    TASK_2 = "task_2"


class WritingEvaluationRequest(BaseModel):
    task_type: WritingTaskType
    question: str = Field(min_length=10, max_length=10_000)
    essay: str = Field(min_length=20, max_length=30_000)
    title: str | None = Field(default=None, max_length=300)
    include_sample_answer: bool = False

    @field_validator("question", "essay")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CriterionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    band: float = Field(ge=0, le=9)
    feedback: str = Field(min_length=1, max_length=4_000)

    @field_validator("band")
    @classmethod
    def require_half_band(cls, value: float) -> float:
        if abs(value * 2 - round(value * 2)) > 1e-9:
            raise ValueError("band must use 0.5 increments")
        return value


class WritingEvaluationResponse(BaseModel):
    id: str
    task_type: WritingTaskType
    overall_band: float = Field(ge=0, le=9)
    task_achievement: CriterionFeedback | None = None
    task_response: CriterionFeedback | None = None
    coherence_and_cohesion: CriterionFeedback
    lexical_resource: CriterionFeedback
    grammatical_range_and_accuracy: CriterionFeedback
    general_feedback: str = Field(min_length=1, max_length=6_000)
    strengths: list[str] = Field(min_length=1, max_length=10)
    improvements: list[str] = Field(min_length=1, max_length=10)
    sample_answer: str | None = Field(default=None, max_length=30_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def task_criterion_matches_task_type(self) -> WritingEvaluationResponse:
        if self.task_type == WritingTaskType.TASK_1:
            if self.task_achievement is None or self.task_response is not None:
                raise ValueError("Task 1 requires task_achievement only")
        elif self.task_response is None or self.task_achievement is not None:
            raise ValueError("Task 2 requires task_response only")
        return self


class ModelWritingEvaluation(BaseModel):
    """Strict JSON contract returned by the language model."""

    model_config = ConfigDict(extra="forbid")

    task_fulfilment: CriterionFeedback
    coherence_and_cohesion: CriterionFeedback
    lexical_resource: CriterionFeedback
    grammatical_range_and_accuracy: CriterionFeedback
    general_feedback: str = Field(min_length=1, max_length=6_000)
    strengths: list[str] = Field(min_length=1, max_length=10)
    improvements: list[str] = Field(min_length=1, max_length=10)
    sample_answer: str | None = Field(default=None, max_length=30_000)


class WritingEvaluationRecord(BaseModel):
    request: WritingEvaluationRequest
    response: WritingEvaluationResponse
    model: str
    prompt_version: str
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    elapsed_ms: int = Field(0, ge=0)
    retries: int = Field(0, ge=0)