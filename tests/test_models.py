
import pytest
from pydantic import ValidationError

from app.models import (
    Difficulty,
    GenerationUnit,
    QuestionType,
    ReadingPackage,
    SourceBrief,
    UnitStatus,
    VocabularyEntry,
)


def test_generation_unit_requires_three_distinct_question_types():
    with pytest.raises(ValidationError):
        GenerationUnit(
            id="unit-1", corpus_id="corpus-1", source_chapter_ids=["c1"],
            source_text_hash="abc", difficulty=Difficulty.STANDARD,
            question_types=[QuestionType.MATCHING_HEADINGS] * 3,
            config_snapshot={}, prompt_version="1", status=UnitStatus.INDEXED,
        )


def test_reading_package_question_numbers_are_contiguous():
    package = ReadingPackage.model_validate({
        "unit": {
            "id": "unit-1", "corpus_id": "corpus-1", "source_chapter_ids": ["c1"],
            "source_text_hash": "abc", "difficulty": "standard",
            "question_types": ["matching_headings", "multiple_choice", "short_answer"],
            "config_snapshot": {}, "prompt_version": "1", "status": "completed",
        },
        "source_brief": {},
        "passage": {"title": "T", "difficulty": "standard", "word_count": 2, "paragraphs": [], "vocabulary": [], "source_coverage": {}, "author_revision": 0},
        "question_groups": [{"type": "matching_headings", "instructions": "", "questions": [{"number": 1, "prompt": "p", "answer": "a", "acceptable_answers": [], "evidence_paragraph": "A", "evidence_quote": "q", "chinese_explanation": "x", "distractor_explanations": {}}]}],
        "quality_report": {}, "usage_records": [],
    })
    changed = package.model_copy(deep=True)
    changed.question_groups[0].questions[0].number = 8
    with pytest.raises(ValidationError, match="contiguous"):
        ReadingPackage.model_validate(changed.model_dump())


def test_source_chapter_rejects_duplicate_paragraph_labels():
    from app.models import SourceChapter

    with pytest.raises(ValidationError, match="unique"):
        SourceChapter(id="c1", ordinal=1, paragraphs=[
            {"id": "p1", "label": "A", "text": "one"},
            {"id": "p2", "label": "A", "text": "two"},
        ])


def test_source_brief_rejects_duplicate_item_ids_across_categories():
    with pytest.raises(ValidationError, match="item IDs"):
        SourceBrief(
            core_facts=[{"id": "same", "text": "fact"}],
            core_claims=[{"id": "same", "text": "claim"}],
        )


def test_vocabulary_accepts_provider_term_alias():
    entry = VocabularyEntry.model_validate({"term": "tomb", "chinese_meaning": "墓穴"})
    assert entry.word == "tomb"
