from __future__ import annotations

from app.models import Corpus, Difficulty, GenerationUnit, QuestionType, SourceBrief, UnitStatus
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository


def test_stage_attempt_round_trips_completion_payload_and_artifact(tmp_path):
    repository, unit = make_repository(tmp_path)
    store = ArtifactStore(tmp_path / "output")
    payload = SourceBrief(prohibited_inventions=["invented detail"])
    artifact = store.write_stage_payload(unit.id, "author", 1, payload)

    repository.create_stage_attempt(unit.id, "author", 1, cache_key="cache-1")
    assert repository.complete_stage_attempt(
        unit.id, "author", 1, artifact_path=str(artifact), payload=payload,
    )

    attempt = repository.get_stage_attempt(unit.id, "author", 1)
    assert attempt is not None
    assert attempt.status == "completed"
    assert attempt.artifact_path == str(artifact)
    assert attempt.payload == payload.model_dump(mode="json")


def test_stage_cache_reuses_completed_attempt_and_rejects_duplicate_commit(tmp_path):
    repository, first_unit = make_repository(tmp_path)
    second_unit = first_unit.model_copy(update={"id": "unit-2"})
    repository.add_unit(second_unit)
    repository.create_stage_attempt(first_unit.id, "author", 1, cache_key="same-key")
    repository.create_stage_attempt(second_unit.id, "author", 1, cache_key="same-key")

    assert repository.complete_stage_attempt(
        first_unit.id, "author", 1, artifact_path="stage_payloads/unit-1/author-attempt-1.json", payload={"answer": 1},
    )
    cached = repository.get_cached_stage("same-key")
    assert cached is not None
    assert cached.unit_id == first_unit.id
    assert not repository.complete_stage_attempt(
        second_unit.id, "author", 1, artifact_path="stage_payloads/unit-2/author-attempt-1.json", payload={"answer": 2},
    )
    assert repository.get_stage_attempt(second_unit.id, "author", 1).status == "running"


def test_stage_attempt_records_failure_without_affecting_other_attempts(tmp_path):
    repository, unit = make_repository(tmp_path)
    repository.create_stage_attempt(unit.id, "author", 1)
    repository.create_stage_attempt(unit.id, "examiner", 1)

    assert repository.fail_stage_attempt(unit.id, "author", 1, error="timeout", payload={"retry": True})
    assert repository.get_stage_attempt(unit.id, "author", 1).error == "timeout"
    assert repository.get_stage_attempt(unit.id, "examiner", 1).status == "running"


def test_stage_attempt_updates_pending_metadata(tmp_path):
    repository, unit = make_repository(tmp_path)
    repository.create_stage_attempt(unit.id, "author", 1)

    assert repository.update_stage_attempt(
        unit.id, "author", 1, cache_key="cache-1", payload={"request": "saved"}, error="retrying",
    )

    attempt = repository.get_stage_attempt(unit.id, "author", 1)
    assert attempt.cache_key == "cache-1"
    assert attempt.payload == {"request": "saved"}
    assert attempt.error == "retrying"


def make_repository(tmp_path):
    database = Database(tmp_path / "state.db")
    database.create_schema()
    repository = Repository(database)
    repository.add_corpus(Corpus(
        id="corpus-1", name="Corpus", source_path="source.txt", source_hash="hash",
        format="txt", chapter_count=1, parser_version="v1",
    ))
    unit = GenerationUnit(
        id="unit-1", corpus_id="corpus-1", source_chapter_ids=["chapter-1"], source_text_hash="source-hash",
        difficulty=Difficulty.STANDARD,
        question_types=[QuestionType.MATCHING_HEADINGS, QuestionType.MULTIPLE_CHOICE, QuestionType.SHORT_ANSWER],
        config_snapshot={}, prompt_version="p1", status=UnitStatus.INDEXED,
    )
    repository.add_unit(unit)
    return repository, unit
