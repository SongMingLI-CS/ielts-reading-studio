from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import AppConfig, redact_secrets
from app.models import GenerationUnit, QuestionGroup, ReadingPassage, SourceBrief
from app.planning.units import question_total

from .base import AgentSchemaError, ModelRequest, ModelResult
from .prompts import (
    EXAMINER_ASSESSMENT_SYSTEM,
    EXAMINER_PROMPT_VERSION,
    EXAMINER_REVIEW_SYSTEM,
)


class ReviewIssue(BaseModel):
    code: str
    message: str
    affected_ids: list[str] = Field(default_factory=list)


class PassageReview(BaseModel):
    passed: bool
    issues: list[ReviewIssue] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)


class AssessmentPayload(BaseModel):
    question_groups: list[QuestionGroup]


class ExaminerAgent:
    """Agent B: independent passage review and assessment authoring."""

    prompt_version = EXAMINER_PROMPT_VERSION
    review_prompt_version = "2"
    assessment_prompt_version = EXAMINER_PROMPT_VERSION

    def __init__(self, provider: Any, config: AppConfig) -> None:
        self.provider = provider
        self.config = config
        self.results: list[ModelResult] = []

    @property
    def last_result(self) -> ModelResult | None:
        return self.results[-1] if self.results else None

    def review_passage(
        self,
        unit: GenerationUnit,
        brief: SourceBrief,
        source_text: str,
        passage: ReadingPassage,
    ) -> PassageReview:
        user = _json_context(
            "Review the passage against its source. Return review JSON only.",
            unit=_unit_context(unit),
            source_brief=brief.model_dump(mode="json"),
            source_text=source_text,
            passage=passage.model_dump(mode="json"),
        )
        return self._call(
            ModelRequest(
                stage="examiner_passage_review",
                model=self.config.examiner_model,
                system=EXAMINER_REVIEW_SYSTEM,
                user=user,
                max_tokens=4000,
                temperature=0.1,
            ),
            PassageReview,
        )

    def build_assessment(
        self,
        unit: GenerationUnit,
        passage: ReadingPassage,
    ) -> list[QuestionGroup]:
        counts = _question_counts(unit)
        user = (
            "Build the assessment from this frozen passage only. Return assessment JSON only.\n"
            f"Frozen passage JSON:\n{passage.model_dump_json()}\n"
            f"Requested JSON configuration:\n{json.dumps({'difficulty': unit.difficulty.value, 'question_counts': counts}, sort_keys=True)}"
        )
        payload = self._call(
            ModelRequest(
                stage="examiner_assessment",
                model=self.config.examiner_model,
                system=EXAMINER_ASSESSMENT_SYSTEM,
                user=user,
                max_tokens=6500,
                temperature=0.2,
            ),
            AssessmentPayload,
        )
        groups = payload.question_groups
        expected = [value.value for value in unit.question_types]
        actual = [group.type.value for group in groups]
        if len(groups) != 3 or len(set(actual)) != 3 or set(actual) != set(expected):
            raise AgentSchemaError(
                f"examiner_assessment must return exactly the requested groups: {expected}; got {actual}"
            )
        by_type = {group.type.value: group for group in groups}
        ordered = [by_type[value] for value in expected]
        number = 1
        normalized: list[QuestionGroup] = []
        for group in ordered:
            questions = []
            for question in group.questions:
                questions.append(question.model_copy(update={"number": number}))
                number += 1
            normalized.append(group.model_copy(update={"questions": questions}))
        return normalize_completion_limits(normalized)

    def repair_assessment(
        self,
        unit: GenerationUnit,
        passage: ReadingPassage,
        groups: list[QuestionGroup],
        failed_group_ids: list[str],
        issues: list[dict[str, Any]],
    ) -> list[QuestionGroup]:
        requested = set(failed_group_ids)
        if not requested:
            return list(groups)
        user = (
            "Repair only the requested failed question groups. Return those replacement groups as JSON.\n"
            f"Frozen passage JSON:\n{passage.model_dump_json()}\n"
            f"Repair JSON context:\n{json.dumps({'failed_group_ids': failed_group_ids, 'issues': issues, 'current_groups': [group.model_dump(mode='json') for group in groups]}, ensure_ascii=False, sort_keys=True)}"
        )
        payload = self._call(
            ModelRequest(
                stage="examiner_assessment_revision",
                model=self.config.examiner_model,
                system=EXAMINER_ASSESSMENT_SYSTEM,
                user=user,
                max_tokens=5000,
                temperature=0.1,
            ),
            AssessmentPayload,
        )
        replacements = {group.type.value: group for group in payload.question_groups}
        if set(replacements) != requested:
            raise AgentSchemaError(
                "examiner_assessment_revision returned missing or unrequested groups"
            )
        existing = {group.type.value for group in groups}
        if not requested <= existing:
            raise AgentSchemaError(
                "examiner_assessment_revision requested unknown group"
            )
        return normalize_completion_limits(
            [replacements.get(group.type.value, group) for group in groups]
        )

    def _call(self, request: ModelRequest, model_type: type[BaseModel]):
        result = self.provider.complete_json(request)
        self.results.append(result)
        try:
            return model_type.model_validate(result.payload)
        except ValidationError as exc:
            message = redact_secrets(exc)
            raise AgentSchemaError(
                f"{request.stage} schema validation failed: {message}"
            ) from None


def _unit_context(unit: GenerationUnit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "difficulty": unit.difficulty.value,
        "question_types": [value.value for value in unit.question_types],
    }


def _question_counts(unit: GenerationUnit) -> dict[str, int]:
    total = question_total(unit.difficulty)
    quotient, remainder = divmod(total, len(unit.question_types))
    return {
        question_type.value: quotient + (1 if index < remainder else 0)
        for index, question_type in enumerate(unit.question_types)
    }


def _json_context(instruction: str, **values: Any) -> str:
    return f"{instruction}\nJSON input:\n{json.dumps(values, ensure_ascii=False, sort_keys=True)}"


def normalize_completion_limits(groups: list[QuestionGroup]) -> list[QuestionGroup]:
    """Align a completion group's stated limit with valid IELTS limits.

    Providers occasionally return a verbatim three-word answer while leaving
    the group at two words. Raising the limit to three is preferable to two
    extra model calls and keeps the answer grounded in the frozen passage.
    Longer answers still go through the normal repair loop.
    """
    completion_types = {
        "sentence_completion",
        "summary_completion",
        "short_answer",
    }
    labels = {1: "ONE", 2: "TWO", 3: "THREE"}
    normalized: list[QuestionGroup] = []
    for group in groups:
        if group.type.value not in completion_types:
            normalized.append(group)
            continue
        answers = [question.answer for question in group.questions if question.answer]
        required = max(
            (len(re.findall(r"\b[\w'’-]+\b", answer)) for answer in answers), default=1
        )
        if required <= 3 and (group.word_limit or 0) < required:
            instructions = re.sub(
                r"NO MORE THAN (?:ONE|TWO|THREE|\d+) WORDS",
                f"NO MORE THAN {labels[required]} WORDS",
                group.instructions,
                flags=re.IGNORECASE,
            )
            group = group.model_copy(
                update={"word_limit": required, "instructions": instructions}
            )
        limit = group.word_limit or 0
        questions = [
            question.model_copy(
                update={
                    "acceptable_answers": [
                        answer
                        for answer in question.acceptable_answers
                        if len(re.findall(r"\b[\w'’-]+\b", answer)) <= limit
                    ]
                }
            )
            for question in group.questions
        ]
        group = group.model_copy(update={"questions": questions})
        normalized.append(group)
    return normalized
