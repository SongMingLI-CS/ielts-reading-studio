from __future__ import annotations

import pytest

from app.models import (
    Difficulty,
    GenerationUnit,
    QualityReport,
    ReadingPackage,
    ReadingPassage,
    SourceBrief,
    UnitStatus,
    VocabularyEntry,
)
from tests.fixtures.agent_payloads import (
    assessment_payload,
    brief_payload,
    passage_payload,
)


@pytest.fixture
def valid_package():
    passage_data = passage_payload()
    passage_data["vocabulary"] = [
        VocabularyEntry(
            word="supply",
            pronunciation="/səˈplaɪ/",
            part_of_speech="noun",
            chinese_meaning="供应",
            collocations=["water supply"],
            example="The town improved its water supply.",
        ).model_dump(mode="json")
    ]
    groups = assessment_payload()["question_groups"]
    unit = GenerationUnit(
        id="unit-1",
        corpus_id="corpus-1",
        source_chapter_ids=["chapter-1"],
        source_text_hash="hash",
        difficulty=Difficulty.STANDARD,
        question_types=[group["type"] for group in groups],
        config_snapshot={},
        prompt_version="1",
        status=UnitStatus.COMPLETED,
    )
    return ReadingPackage(
        unit=unit,
        source_brief=SourceBrief.model_validate(brief_payload()),
        passage=ReadingPassage.model_validate(passage_data),
        question_groups=groups,
        quality_report=QualityReport(passed=True),
    )
