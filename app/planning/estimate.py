from __future__ import annotations

from math import ceil
from typing import NamedTuple

from pydantic import BaseModel, Field

from app.models import GenerationUnit

# One generation unit always needs a brief, a passage, a passage review and an assessment.
BASE_CALLS = 4


class CallProfile(NamedTuple):
    """One model call: how much of the source it reads, plus fixed input and output tokens."""

    name: str
    source_share: float
    extra_input_tokens: int
    output_tokens: int


GENERATION_CALLS: tuple[CallProfile, ...] = (
    CallProfile("brief", 1.0, 0, 500),
    CallProfile("passage", 0.6, 500, 1300),
    CallProfile("passage_review", 1.0, 1300, 700),
    CallProfile("assessment", 0.6, 0, 1000),
)

# One author revision rewrites the passage and must be reviewed again, so it costs
# exactly the passage and review calls.
AUTHOR_REVISION_CALLS: tuple[CallProfile, ...] = GENERATION_CALLS[1:3]
# One examiner revision repairs only the failed question groups.
EXAMINER_REVISION_CALLS: tuple[CallProfile, ...] = (CallProfile("assessment_repair", 0.6, 200, 900),)

# Chinese source text converts to roughly one token per 2.2 characters at the
# optimistic end and one token per 1.4 characters at the pessimistic end.
MIN_CHARACTERS_PER_TOKEN = 2.2
MAX_CHARACTERS_PER_TOKEN = 1.4


class RunEstimate(BaseModel):
    """Token and request bounds for a planned run. Never carries invented currency."""

    unit_count: int = 0
    per_unit_minimum_requests: int = 0
    per_unit_maximum_requests: int = 0
    minimum_requests: int = 0
    maximum_requests: int = 0
    minimum_tokens: int = 0
    maximum_tokens: int = 0
    limited_source_units: int = 0
    pricing_available: bool = False
    notes: list[str] = Field(default_factory=list)


def estimate_run(
    units: list[GenerationUnit],
    author_revisions: int = 2,
    examiner_revisions: int = 2,
) -> RunEstimate:
    """Estimate request and token ranges for a batch without calling the API."""
    author_revisions = max(0, int(author_revisions))
    examiner_revisions = max(0, int(examiner_revisions))

    optional_calls: list[CallProfile] = []
    for _ in range(author_revisions):
        optional_calls.extend(AUTHOR_REVISION_CALLS)
    for _ in range(examiner_revisions):
        optional_calls.extend(EXAMINER_REVISION_CALLS)

    minimum_tokens = 0
    maximum_tokens = 0
    limited_source_units = 0
    for unit in units:
        if unit.limited_source:
            limited_source_units += 1
        characters = unit.source_character_count
        for call in (*GENERATION_CALLS, *optional_calls):
            low, high = token_range(characters * call.source_share)
            minimum_tokens += low + call.extra_input_tokens + call.output_tokens
            maximum_tokens += high + call.extra_input_tokens + call.output_tokens

    per_unit_minimum = BASE_CALLS
    per_unit_maximum = (
        BASE_CALLS + len(AUTHOR_REVISION_CALLS) * author_revisions + len(EXAMINER_REVISION_CALLS) * examiner_revisions
    )

    notes = [
        f"base calls per unit: {BASE_CALLS} (brief, passage, passage review, assessment)",
        f"revision limits: author {author_revisions}, examiner {examiner_revisions}",
        "pricing unavailable: no per-token prices configured",
    ]
    if limited_source_units:
        notes.append(f"limited_source_units: {limited_source_units}")

    return RunEstimate(
        unit_count=len(units),
        per_unit_minimum_requests=per_unit_minimum,
        per_unit_maximum_requests=per_unit_maximum,
        minimum_requests=len(units) * per_unit_minimum,
        maximum_requests=len(units) * per_unit_maximum,
        minimum_tokens=minimum_tokens,
        maximum_tokens=maximum_tokens,
        limited_source_units=limited_source_units,
        pricing_available=False,
        notes=notes,
    )


def token_range(characters: float) -> tuple[int, int]:
    """Input-token bounds for a number of Chinese source characters."""
    if characters <= 0:
        return 0, 0
    return ceil(characters / MIN_CHARACTERS_PER_TOKEN), ceil(characters / MAX_CHARACTERS_PER_TOKEN)
