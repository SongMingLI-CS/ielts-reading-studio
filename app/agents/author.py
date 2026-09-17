from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import AppConfig, redact_secrets
from app.models import GenerationUnit, ReadingPassage, SourceBrief

from .base import AgentSchemaError, ModelRequest, ModelResult
from .prompts import (
    AUTHOR_BRIEF_PROMPT_VERSION,
    AUTHOR_BRIEF_SYSTEM,
    AUTHOR_PASSAGE_PROMPT_VERSION,
    AUTHOR_PASSAGE_SYSTEM,
)

T = TypeVar("T", bound=BaseModel)
WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?")
SENTENCE_RE = re.compile(r'.+?(?:[.!?](?:["”’])?(?=\s|$)|$)', re.DOTALL)


class AuthorAgent:
    """Agent A: source brief, passage drafting, and passage-only revisions."""

    brief_prompt_version = AUTHOR_BRIEF_PROMPT_VERSION
    passage_prompt_version = AUTHOR_PASSAGE_PROMPT_VERSION

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
                max_tokens=4000,
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
                max_tokens=4200,
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
                max_tokens=4200,
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
            raise AgentSchemaError(
                f"{request.stage} schema validation failed: {message}"
            ) from None


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
    # These are workflow-derived fields owned by the application. Normalizing
    # them prevents valid content from being discarded over model arithmetic.
    passage = _fit_passage_length(passage)
    word_count = sum(
        len(WORD_RE.findall(paragraph.text)) for paragraph in passage.paragraphs
    )
    return passage.model_copy(
        update={"author_revision": expected_revision, "word_count": word_count}
    )


def _fit_passage_length(
    passage: ReadingPassage, *, target_words: int = 860
) -> ReadingPassage:
    """Keep an overlong model response usable without another paid revision.

    The model is still asked to produce the correct length. This is a final,
    paragraph-preserving guard for providers that repeatedly ignore the hard
    word limit. Source mappings and paragraph labels remain unchanged.
    """
    counts = [len(WORD_RE.findall(paragraph.text)) for paragraph in passage.paragraphs]
    total = sum(counts)
    if total <= 900:
        return passage

    budgets = [max(1, count * target_words // total) for count in counts]
    remaining = target_words - sum(budgets)
    for index in sorted(range(len(counts)), key=counts.__getitem__, reverse=True):
        if remaining <= 0:
            break
        budgets[index] += 1
        remaining -= 1

    chunks_by_paragraph = [
        _sentence_chunks(paragraph.text) for paragraph in passage.paragraphs
    ]
    selected_counts: list[int] = []
    fitted_count = 0
    for chunks, budget in zip(chunks_by_paragraph, budgets, strict=True):
        selected = 0
        selected_words = 0
        for chunk in chunks:
            chunk_words = len(WORD_RE.findall(chunk))
            if selected and selected_words + chunk_words > budget:
                break
            selected += 1
            selected_words += chunk_words
        selected_counts.append(selected)
        fitted_count += selected_words

    # Sentence rounding can undershoot. Add the next complete sentence from
    # each paragraph until the passage reaches the minimum, never exceeding
    # the validator's upper bound.
    while fitted_count < 700:
        candidates = []
        for index, chunks in enumerate(chunks_by_paragraph):
            selected = selected_counts[index]
            if selected >= len(chunks):
                continue
            words = len(WORD_RE.findall(chunks[selected]))
            if fitted_count + words <= 900:
                candidates.append((words, index))
        if not candidates:
            break
        words, index = min(candidates)
        selected_counts[index] += 1
        fitted_count += words

    paragraphs = [
        paragraph.model_copy(update={"text": " ".join(chunks[:selected]).strip()})
        for paragraph, chunks, selected in zip(
            passage.paragraphs, chunks_by_paragraph, selected_counts, strict=True
        )
    ]
    return passage.model_copy(update={"paragraphs": paragraphs})


def _sentence_chunks(text: str) -> list[str]:
    chunks = [
        match.group(0).strip()
        for match in SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    ]
    if chunks and chunks[-1][-1] not in '.!?"”’':
        chunks[-1] = chunks[-1].rstrip(",;:—- ") + "."
    return chunks
