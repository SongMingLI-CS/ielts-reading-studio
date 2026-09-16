from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import AppConfig, redact_secrets
from app.models import GenerationUnit, ReadingPassage, SourceBrief

from .base import AgentSchemaError, ModelRequest, ModelResult
from .prompts import AUTHOR_BRIEF_SYSTEM, AUTHOR_PASSAGE_SYSTEM, AUTHOR_PROMPT_VERSION

T = TypeVar("T", bound=BaseModel)


class AuthorAgent:
    """Agent A: source brief, passage drafting, and passage-only revisions."""

    prompt_version = AUTHOR_PROMPT_VERSION

    def __init__(self, provider: Any, config: AppConfig) -> None:
        self.provider = provider
        self.config = config
        self.results: list[ModelResult] = []

    @property
    def last_result(self) -> ModelResult | None:
        return self.results[-1] if self.results else None

    def create_brief(self, unit: GenerationUnit, source_text: str) -> SourceBrief:
        user = _json_context(
            "Create the source brief from this input.",
            unit=_unit_context(unit),
            source_text=source_text,
        )
        return self._call(
            ModelRequest(
                stage="author_brief",
                model=self.config.author_model,
                system=AUTHOR_BRIEF_SYSTEM,
                user=user,
                max_tokens=2500,
                temperature=0.1,
            ),
            SourceBrief,
        )

    def write_passage(
        self,
        unit: GenerationUnit,
        brief: SourceBrief,
        source_text: str,
    ) -> ReadingPassage:
        user = _json_context(
            "Write the passage. Return ReadingPassage JSON only.",
            unit=_unit_context(unit),
            source_brief=brief.model_dump(mode="json"),
            source_text=source_text,
            author_revision=0,
        )
        passage = self._call(
            ModelRequest(
                stage="author_passage",
                model=self.config.author_model,
                system=AUTHOR_PASSAGE_SYSTEM,
                user=user,
                max_tokens=5000,
                temperature=0.3,
            ),
            ReadingPassage,
        )
        return _validate_passage_contract(unit, passage, expected_revision=0)

    def revise_passage(
        self,
        unit: GenerationUnit,
        brief: SourceBrief,
        source_text: str,
        passage: ReadingPassage,
        issues: list[dict[str, Any]],
    ) -> ReadingPassage:
        user = _json_context(
            "Revise only the identified passage problems. Return complete ReadingPassage JSON.",
            unit=_unit_context(unit),
            source_brief=brief.model_dump(mode="json"),
            source_text=source_text,
            previous_passage=passage.model_dump(mode="json"),
            passage_issues=issues,
            author_revision=passage.author_revision + 1,
        )
        revised = self._call(
            ModelRequest(
                stage="author_passage_revision",
                model=self.config.author_model,
                system=AUTHOR_PASSAGE_SYSTEM,
                user=user,
                max_tokens=5000,
                temperature=0.2,
            ),
            ReadingPassage,
        )
        return _validate_passage_contract(
            unit,
            revised,
            expected_revision=passage.author_revision + 1,
        )

    def _call(self, request: ModelRequest, model_type: type[T]) -> T:
        result = self.provider.complete_json(request)
        self.results.append(result)
        try:
            return model_type.model_validate(result.payload)
        except ValidationError as exc:
            message = redact_secrets(exc)
            raise AgentSchemaError(f"{request.stage} schema validation failed: {message}") from None


def _unit_context(unit: GenerationUnit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "difficulty": unit.difficulty.value,
        "question_types": [value.value for value in unit.question_types],
        "limited_source": unit.limited_source,
    }


def _json_context(instruction: str, **values: Any) -> str:
    return f"{instruction}\nJSON input:\n{json.dumps(values, ensure_ascii=False, sort_keys=True)}"


def _validate_passage_contract(
    unit: GenerationUnit,
    passage: ReadingPassage,
    *,
    expected_revision: int,
) -> ReadingPassage:
    if passage.difficulty != unit.difficulty:
        raise AgentSchemaError(
            f"author_passage difficulty must be {unit.difficulty.value}, got {passage.difficulty.value}"
        )
    if passage.author_revision != expected_revision:
        raise AgentSchemaError(
            f"author_passage revision must be {expected_revision}, got {passage.author_revision}"
        )
    return passage
