from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Corpus, Difficulty, GenerationUnit, QuestionType, UnitStatus
from app.storage.database import Database
from app.storage.repositories import Repository


@pytest.fixture
def repository(tmp_path):
    database = Database(tmp_path / "state.db")
    database.create_schema()
    repository = Repository(database)
    repository.add_corpus(
        Corpus(
            id="corpus-1", name="Corpus", source_path="source.txt", source_hash="hash",
            format="txt", chapter_count=1, parser_version="v1",
        )
    )
    return repository


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


def test_transition_allows_only_one_concurrent_claim(repository, unit):
    repository.add_unit(unit)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(
            lambda _: repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING),
            range(2),
        ))

    assert sorted(claims) == [False, True]
    assert repository.get_unit(unit.id).status == UnitStatus.AUTHOR_GENERATING


def test_unit_requires_an_existing_corpus(repository, unit):
    orphan = unit.model_copy(update={"corpus_id": "missing"})

    with pytest.raises(IntegrityError):
        repository.add_unit(orphan)


def test_jobs_and_corpus_approvals_are_authoritative_state(repository):
    repository.create_job("job-1", "corpus-1", "queued", {"difficulty": "standard"})
    assert repository.get_job("job-1") == {
        "id": "job-1", "corpus_id": "corpus-1", "status": "queued",
        "payload": {"difficulty": "standard"},
    }
    assert repository.update_job("job-1", status="paused")
    repository.record_corpus_approval("approval-1", "corpus-1", "approved", {"by": "tester"})
    assert repository.get_corpus_approval("approval-1") == {
        "id": "approval-1", "corpus_id": "corpus-1", "status": "approved",
        "payload": {"by": "tester"},
    }
