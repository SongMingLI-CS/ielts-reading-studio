from __future__ import annotations

from app.models import UnitStatus

ALLOWED_TRANSITIONS: dict[UnitStatus, frozenset[UnitStatus]] = {
    UnitStatus.INDEXED: frozenset({UnitStatus.AUTHOR_GENERATING, UnitStatus.PAUSED, UnitStatus.CANCELLED}),
    UnitStatus.AUTHOR_GENERATING: frozenset(
        {UnitStatus.PASSAGE_REVIEWING, UnitStatus.FAILED, UnitStatus.PAUSED}
    ),
    UnitStatus.PASSAGE_REVIEWING: frozenset(
        {
            UnitStatus.AUTHOR_REVISION_REQUIRED,
            UnitStatus.EXAMINER_GENERATING,
            UnitStatus.NEEDS_REVIEW,
            UnitStatus.FAILED,
            UnitStatus.PAUSED,
        }
    ),
    UnitStatus.AUTHOR_REVISION_REQUIRED: frozenset(
        {UnitStatus.AUTHOR_GENERATING, UnitStatus.NEEDS_REVIEW, UnitStatus.CANCELLED}
    ),
    UnitStatus.EXAMINER_GENERATING: frozenset(
        {UnitStatus.VALIDATING, UnitStatus.FAILED, UnitStatus.PAUSED}
    ),
    UnitStatus.VALIDATING: frozenset(
        {
            UnitStatus.COMPLETED,
            UnitStatus.EXAMINER_REVISION_REQUIRED,
            UnitStatus.NEEDS_REVIEW,
            UnitStatus.FAILED,
            UnitStatus.PAUSED,
        }
    ),
    UnitStatus.EXAMINER_REVISION_REQUIRED: frozenset(
        {UnitStatus.EXAMINER_GENERATING, UnitStatus.NEEDS_REVIEW, UnitStatus.CANCELLED}
    ),
    UnitStatus.FAILED: frozenset({UnitStatus.AUTHOR_GENERATING, UnitStatus.CANCELLED}),
    UnitStatus.PAUSED: frozenset(
        {
            UnitStatus.AUTHOR_GENERATING,
            UnitStatus.PASSAGE_REVIEWING,
            UnitStatus.EXAMINER_GENERATING,
            UnitStatus.VALIDATING,
            UnitStatus.CANCELLED,
        }
    ),
    UnitStatus.NEEDS_REVIEW: frozenset({UnitStatus.CANCELLED}),
    UnitStatus.COMPLETED: frozenset(),
    UnitStatus.CANCELLED: frozenset(),
}


def can_transition(current: UnitStatus, target: UnitStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def resume_target(status: UnitStatus, *, saved_stage: UnitStatus) -> UnitStatus:
    if status != UnitStatus.PAUSED:
        return status
    if saved_stage not in ALLOWED_TRANSITIONS[UnitStatus.PAUSED]:
        raise ValueError(f"Cannot resume a paused unit at {saved_stage.value}")
    return saved_stage

