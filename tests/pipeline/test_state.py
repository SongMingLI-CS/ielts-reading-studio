from app.models import UnitStatus
from app.pipeline.state import can_transition, resume_target


def test_state_machine_rejects_skipping_examiner():
    assert can_transition(UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING)
    assert not can_transition(UnitStatus.AUTHOR_GENERATING, UnitStatus.COMPLETED)


def test_paused_unit_can_resume_at_saved_stage():
    assert (
        resume_target(UnitStatus.PAUSED, saved_stage=UnitStatus.EXAMINER_GENERATING)
        == UnitStatus.EXAMINER_GENERATING
    )


def test_terminal_statuses_cannot_transition():
    for status in (UnitStatus.COMPLETED, UnitStatus.CANCELLED):
        assert not can_transition(status, UnitStatus.INDEXED)
