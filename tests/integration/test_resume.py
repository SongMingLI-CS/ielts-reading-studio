from __future__ import annotations

from app.config import AppConfig
from app.models import Corpus, Difficulty, GenerationUnit, UnitStatus
from app.pipeline.unit_runner import UnitRunner
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository
from tests.fixtures.agent_payloads import assessment_payload
from tests.pipeline.conftest import FakeAuthor, FakeExaminer, PassValidator


def test_restart_resumes_after_frozen_passage_without_recalling_author(tmp_path):
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "state.db",
    )
    database = Database(config.database_path)
    database.create_schema()
    repository = Repository(database)
    repository.add_corpus(
        Corpus(
            id="corpus-1",
            name="Corpus",
            source_path="source.txt",
            source_hash="hash",
            format="txt",
            chapter_count=1,
            parser_version="1",
        )
    )
    unit = GenerationUnit(
        id="u1",
        corpus_id="corpus-1",
        source_chapter_ids=["c1"],
        source_text_hash="source-hash",
        difficulty=Difficulty.STANDARD,
        question_types=[group["type"] for group in assessment_payload()["question_groups"]],
        config_snapshot={},
        prompt_version="1",
        status=UnitStatus.INDEXED,
    )
    repository.add_unit(unit)
    store = ArtifactStore(config.output_dir)
    author = FakeAuthor()
    examiner = FakeExaminer()

    def crash(stage: str) -> None:
        if stage == "author_passage":
            raise RuntimeError("simulated process stop")

    common = {
        "config": config,
        "repository": repository,
        "store": store,
        "author": author,
        "examiner": examiner,
        "source_text_loader": lambda _unit: "原始中文材料",
        "passage_validator": PassValidator(),
        "question_validator": PassValidator(),
    }
    first = UnitRunner(**common, after_stage=crash)
    try:
        first.run(unit.id)
    except RuntimeError:
        pass
    repository.recover_interrupted_units()

    result = UnitRunner(**common).run(unit.id)

    assert result.status == UnitStatus.COMPLETED
    assert author.write_calls == 1
    assert examiner.assessment_calls == 1
