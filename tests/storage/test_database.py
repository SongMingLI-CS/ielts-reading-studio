from __future__ import annotations

import pytest

from app.models import Difficulty, GenerationUnit, QuestionType, UnitStatus
from app.storage.database import Database
from app.storage.repositories import Repository


@pytest.fixture
def repository(tmp_path):
    database = Database(tmp_path / "state.db")
    database.create_schema()
    return Repository(database)


@pytest.fixture
def unit():
    return GenerationUnit(
        id="unit-1",
        corpus_id="corpus-1",
        source_chapter_ids=["chapter-1"],
        source_text_hash="source-hash",
        difficulty=Difficulty.STANDARD,
        question_types=[
            QuestionType.MATCHING_HEADINGS,
            QuestionType.MULTIPLE_CHOICE,
            QuestionType.SHORT_ANSWER,
        ],
        config_snapshot={"author_model": "deepseek-flash"},
        prompt_version="p1",
        status=UnitStatus.INDEXED,
    )


def test_repository_round_trips_unit_and_compare_and_sets_status(repository, unit):
    repository.add_unit(unit)

    assert repository.get_unit(unit.id) == unit
    assert repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING)
    assert not repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)


def test_recover_running_units(repository, unit):
    repository.add_unit(unit.model_copy(update={"status": UnitStatus.PASSAGE_REVIEWING}))

    recovered = repository.recover_interrupted_units()

    assert recovered == [unit.id]
    assert repository.get_unit(unit.id).status == UnitStatus.AUTHOR_REVISION_REQUIRED
