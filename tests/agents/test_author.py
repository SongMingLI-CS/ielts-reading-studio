from __future__ import annotations

import pytest

from app.agents.author import AuthorAgent
from app.agents.base import AgentSchemaError
from app.models import ReadingPassage, SourceBrief
from tests.fixtures.agent_payloads import brief_payload, passage_payload


@pytest.fixture
def author_agent(recording_provider, agent_config):
    return AuthorAgent(recording_provider, agent_config)


def test_author_creates_brief_with_separate_model_and_prompt(recording_provider, author_agent, unit):
    recording_provider.queue(brief_payload())

    brief = author_agent.create_brief(unit, source_text="原始中文材料")

    assert isinstance(brief, SourceBrief)
    request = recording_provider.requests[-1]
    assert request.stage == "author_brief"
    assert request.model == "author-model"
    assert "原始中文材料" in request.user
    assert "JSON" in request.system


def test_author_never_receives_question_answers(recording_provider, author_agent, unit):
    recording_provider.queue(passage_payload())
    brief = SourceBrief.model_validate(brief_payload())

    passage = author_agent.write_passage(unit, brief, source_text="原文")

    assert isinstance(passage, ReadingPassage)
    request = recording_provider.requests[-1]
    assert "question_groups" not in request.user
    assert "answer_key" not in request.user
    assert "examiner" not in request.user.casefold()


def test_author_revision_only_receives_passage_feedback(recording_provider, author_agent, unit):
    recording_provider.queue(passage_payload(revision=1))
    previous = ReadingPassage.model_validate(passage_payload())

    revised = author_agent.revise_passage(
        unit,
        SourceBrief.model_validate(brief_payload()),
        source_text="原文",
        passage=previous,
        issues=[{"code": "unsupported_fact", "message": "Remove it"}],
    )

    assert revised.author_revision == 1
    prompt = recording_provider.requests[-1].user
    assert "unsupported_fact" in prompt
    assert "question_groups" not in prompt


def test_author_rejects_malformed_schema(recording_provider, author_agent, unit):
    recording_provider.queue({"title": "missing passage fields"})
    with pytest.raises(AgentSchemaError, match="author_passage"):
        author_agent.write_passage(
            unit,
            SourceBrief.model_validate(brief_payload()),
            source_text="原文",
        )


def test_author_retains_usage_result(recording_provider, author_agent, unit):
    recording_provider.queue(brief_payload())
    author_agent.create_brief(unit, source_text="原文")
    assert author_agent.last_result is not None
    assert author_agent.last_result.input_tokens == 10


def test_author_rejects_wrong_difficulty_or_revision(recording_provider, author_agent, unit):
    wrong_difficulty = passage_payload()
    wrong_difficulty["difficulty"] = "advanced"
    recording_provider.queue(wrong_difficulty)
    with pytest.raises(AgentSchemaError, match="difficulty"):
        author_agent.write_passage(
            unit,
            SourceBrief.model_validate(brief_payload()),
            source_text="原文",
        )

    wrong_revision = passage_payload(revision=0)
    recording_provider.queue(wrong_revision)
    with pytest.raises(AgentSchemaError, match="revision"):
        author_agent.revise_passage(
            unit,
            SourceBrief.model_validate(brief_payload()),
            source_text="原文",
            passage=ReadingPassage.model_validate(passage_payload()),
            issues=[],
        )
