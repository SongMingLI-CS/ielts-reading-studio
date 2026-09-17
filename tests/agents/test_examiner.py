from __future__ import annotations

import pytest

from app.agents.base import AgentSchemaError
from app.agents.examiner import ExaminerAgent, PassageReview
from app.models import QuestionGroup, ReadingPassage, SourceBrief
from tests.fixtures.agent_payloads import (
    assessment_payload,
    brief_payload,
    passage_payload,
    review_payload,
)


@pytest.fixture
def examiner_agent(recording_provider, agent_config):
    return ExaminerAgent(recording_provider, agent_config)


def test_examiner_reviews_passage_against_source(
    recording_provider, examiner_agent, unit
):
    recording_provider.queue(review_payload())

    review = examiner_agent.review_passage(
        unit,
        SourceBrief.model_validate(brief_payload()),
        source_text="原始中文材料",
        passage=ReadingPassage.model_validate(passage_payload()),
    )

    assert isinstance(review, PassageReview)
    assert review.passed
    request = recording_provider.requests[-1]
    assert request.stage == "examiner_passage_review"
    assert request.model == "examiner-model"
    assert "原始中文材料" in request.user
    assert request.max_tokens == 4000


def test_examiner_builds_questions_from_frozen_passage(
    recording_provider, examiner_agent, unit
):
    recording_provider.queue(assessment_payload())
    passage = ReadingPassage.model_validate(passage_payload())

    groups = examiner_agent.build_assessment(unit, passage)

    assert all(isinstance(group, QuestionGroup) for group in groups)
    request = recording_provider.requests[-1]
    assert passage.model_dump_json() in request.user
    assert "source_text" not in request.user
    assert "source_brief" not in request.user
    assert len(groups) == 3


def test_examiner_normalizes_group_order_and_question_numbers(
    recording_provider, examiner_agent, unit
):
    payload = assessment_payload()
    payload["question_groups"] = [
        payload["question_groups"][0],
        payload["question_groups"][2],
        payload["question_groups"][1],
    ]
    recording_provider.queue(payload)

    groups = examiner_agent.build_assessment(
        unit, ReadingPassage.model_validate(passage_payload())
    )

    assert [group.type.value for group in groups] == [
        "matching_headings",
        "true_false_not_given",
        "summary_completion",
    ]
    assert [
        question.number for group in groups for question in group.questions
    ] == list(range(1, 13))


def test_examiner_aligns_completion_limit_with_three_word_answer(
    recording_provider, examiner_agent, unit
):
    payload = assessment_payload()
    completion = payload["question_groups"][2]
    completion["instructions"] = "Choose NO MORE THAN TWO WORDS from the passage."
    completion["word_limit"] = 2
    completion["questions"][0]["answer"] = "water thief's hole"
    recording_provider.queue(payload)

    groups = examiner_agent.build_assessment(
        unit, ReadingPassage.model_validate(passage_payload())
    )

    normalized = groups[2]
    assert normalized.word_limit == 3
    assert "THREE WORDS" in normalized.instructions


def test_examiner_drops_acceptable_answers_over_group_limit(
    recording_provider, examiner_agent, unit
):
    payload = assessment_payload()
    completion = payload["question_groups"][2]
    completion["questions"][0]["acceptable_answers"] = [
        "valid pair",
        "too many words here",
    ]
    recording_provider.queue(payload)

    groups = examiner_agent.build_assessment(
        unit, ReadingPassage.model_validate(passage_payload())
    )

    assert groups[2].questions[0].acceptable_answers == ["valid pair"]


def test_examiner_rejects_missing_evidence(recording_provider, examiner_agent, unit):
    recording_provider.queue(assessment_payload(evidence_quote=None))
    with pytest.raises(AgentSchemaError, match="examiner_assessment"):
        examiner_agent.build_assessment(
            unit, ReadingPassage.model_validate(passage_payload())
        )


def test_repair_replaces_only_failed_group(recording_provider, examiner_agent, unit):
    original = [
        QuestionGroup.model_validate(group)
        for group in assessment_payload()["question_groups"]
    ]
    replacement = assessment_payload()["question_groups"][1]
    replacement["questions"][0]["prompt"] = "Repaired question"
    recording_provider.queue({"question_groups": [replacement]})

    repaired = examiner_agent.repair_assessment(
        unit,
        ReadingPassage.model_validate(passage_payload()),
        groups=original,
        failed_group_ids=["true_false_not_given"],
        issues=[{"code": "bad_evidence", "affected_ids": ["2"]}],
    )

    assert repaired[0] == original[0]
    assert repaired[2] == original[2]
    assert repaired[1].questions[0].prompt == "Repaired question"
    prompt = recording_provider.requests[-1].user
    assert "true_false_not_given" in prompt
    assert "source_text" not in prompt


def test_repair_rejects_unrequested_group(recording_provider, examiner_agent, unit):
    replacement = assessment_payload()["question_groups"][0]
    recording_provider.queue({"question_groups": [replacement]})
    groups = [
        QuestionGroup.model_validate(group)
        for group in assessment_payload()["question_groups"]
    ]

    with pytest.raises(AgentSchemaError, match="unrequested"):
        examiner_agent.repair_assessment(
            unit,
            ReadingPassage.model_validate(passage_payload()),
            groups=groups,
            failed_group_ids=["true_false_not_given"],
            issues=[],
        )
