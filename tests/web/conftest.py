from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.models import (
    QualityReport,
    ReadingPackage,
    ReadingPassage,
    SourceBrief,
    UnitStatus,
    VocabularyEntry,
)
from app.pipeline.service import ReadingStudioService
from app.web.app import create_app
from tests.fixtures.agent_payloads import (
    assessment_payload,
    brief_payload,
    passage_payload,
)


@pytest.fixture
def web_service(tmp_path):
    config = AppConfig(
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    return ReadingStudioService(config)


@pytest.fixture
def client(web_service):
    with TestClient(create_app(config=web_service.config, service=web_service)) as value:
        yield value


@pytest.fixture
def sample_txt(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text(
        "第一章 开始\n" + "正文。" * 300 + "\n第二章 后续\n" + "后文。" * 300,
        encoding="utf-8",
    )
    return path


@pytest.fixture
def completed_unit(web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    unit = manifest.units[0]
    web_service.repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)
    groups = assessment_payload()["question_groups"]
    passage_data = passage_payload()
    for paragraph in passage_data["paragraphs"]:
        paragraph["text"] = "Clean water requires careful local planning."
    for group in groups:
        for question in group["questions"]:
            question["evidence_quote"] = "Clean water"
    package = ReadingPackage(
        unit=unit.model_copy(update={"status": UnitStatus.COMPLETED}),
        source_brief=SourceBrief.model_validate(brief_payload()),
        passage=ReadingPassage.model_validate(passage_data),
        question_groups=groups,
        quality_report=QualityReport(passed=True),
    )
    web_service.store.write_package(unit.id, package)
    return SimpleNamespace(id=unit.id, package=package, corpus_id=unit.corpus_id)


@pytest.fixture
def needs_review_unit(web_service, completed_unit):
    original = web_service.repository.get_unit(completed_unit.id)
    unit = original.model_copy(update={"id": "needs-review", "status": UnitStatus.NEEDS_REVIEW, "ordinal": 99})
    web_service.repository.add_unit(unit)
    return unit


@pytest.fixture
def vocabulary_unit(web_service, completed_unit):
    """A completed unit whose package carries a small vocabulary list."""
    entries = [
        VocabularyEntry(
            word="conservation",
            pronunciation="ˌkɒnsəˈveɪʃn",
            part_of_speech="n.",
            chinese_meaning="保护；节约",
            collocations=["water conservation"],
            example="Water conservation matters in dry regions.",
        ),
        VocabularyEntry(word="conserve", part_of_speech="v.", chinese_meaning="节约；保存"),
        VocabularyEntry(word="sustainable", part_of_speech="adj.", chinese_meaning="可持续的"),
        VocabularyEntry(word="municipal", part_of_speech="", chinese_meaning="市政的"),
        VocabularyEntry(word="infrastructure", part_of_speech="n.", chinese_meaning="基础设施"),
        VocabularyEntry(word="scarcity", part_of_speech="n.", chinese_meaning="短缺"),
    ]
    package = completed_unit.package.model_copy(deep=True)
    passage = package.passage.model_copy(update={"vocabulary": entries})
    package = package.model_copy(update={"passage": passage})
    web_service.store.write_package(completed_unit.id, package)
    return SimpleNamespace(id=completed_unit.id, package=package, corpus_id=completed_unit.corpus_id)
