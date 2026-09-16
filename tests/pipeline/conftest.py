from __future__ import annotations

from copy import deepcopy

import pytest

from app.agents.examiner import PassageReview, ReviewIssue
from app.config import AppConfig
from app.models import (
    Corpus,
    Difficulty,
    GenerationUnit,
    QualityIssue,
    QualityReport,
    QuestionGroup,
    ReadingPassage,
    SourceBrief,
    UnitStatus,
)
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository
from tests.fixtures.agent_payloads import (
    assessment_payload,
    brief_payload,
    passage_payload,
)


class FakeAuthor:
    def __init__(self):
        self.brief_calls = 0
        self.write_calls = 0
        self.revise_calls = 0
        self.last_result = None

    def create_brief(self, unit, source_text):
        self.brief_calls += 1
        return SourceBrief.model_validate(brief_payload())

    def write_passage(self, unit, brief, source_text):
        self.write_calls += 1
        return ReadingPassage.model_validate(passage_payload())

    def revise_passage(self, unit, brief, source_text, passage, issues):
        self.revise_calls += 1
        payload = passage_payload(revision=self.revise_calls)
        return ReadingPassage.model_validate(payload)


class FakeExaminer:
    def __init__(self):
        self.review_results = [PassageReview(passed=True)]
        self.review_calls = 0
        self.assessment_calls = 0
        self.repair_calls = 0
        self.repaired_group_ids: list[list[str]] = []
        self.last_result = None

    def review_passage(self, unit, brief, source_text, passage):
        result = self.review_results[min(self.review_calls, len(self.review_results) - 1)]
        self.review_calls += 1
        return result

    def build_assessment(self, unit, passage):
        self.assessment_calls += 1
        return [QuestionGroup.model_validate(value) for value in assessment_payload()["question_groups"]]

    def repair_assessment(self, unit, passage, groups, failed_group_ids, issues):
        self.repair_calls += 1
        self.repaired_group_ids.append(list(failed_group_ids))
        return deepcopy(groups)


class PassValidator:
    def __call__(self, *args, **kwargs):
        return QualityReport(passed=True)


class FailOnceQuestionValidator:
    def __init__(self, group_id: str):
        self.group_id = group_id
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return QualityReport(
                passed=False,
                issues=[
                    QualityIssue(
                        code="bad_evidence",
                        message="bad evidence",
                        stage="questions",
                        affected_ids=[self.group_id],
                    )
                ],
            )
        return QualityReport(passed=True)


@pytest.fixture
def pipeline_app(tmp_path):
    config = AppConfig(base_dir=tmp_path, output_dir=tmp_path / "output", database_path=tmp_path / "state.db")
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
        ordinal=1,
    )
    repository.add_unit(unit)
    return config, repository, ArtifactStore(config.output_dir), unit


@pytest.fixture
def fakes():
    return type(
        "Fakes",
        (),
        {"author": FakeAuthor(), "examiner": FakeExaminer()},
    )()


def review_failed(code: str) -> PassageReview:
    return PassageReview(
        passed=False,
        issues=[ReviewIssue(code=code, message=code, affected_ids=["A"])],
        requested_changes=[code],
    )


def review_passed() -> PassageReview:
    return PassageReview(passed=True)
