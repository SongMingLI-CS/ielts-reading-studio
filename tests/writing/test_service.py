from __future__ import annotations

import pytest

from app.agents.base import AgentSchemaError, ModelResult
from app.config import AppConfig
from app.storage.database import Database
from app.storage.repositories import Repository
from app.writing.models import WritingEvaluationRequest
from app.writing.service import WritingEvaluationService


class StubProvider:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        return ModelResult(
            payload=self.payload,
            raw_text="{}",
            input_tokens=100,
            output_tokens=200,
            elapsed_ms=30,
            model="writing-test-model",
        )


def evaluation_payload(**overrides):
    payload = {
        "task_fulfilment": {"band": 7.0, "feedback": "The position is developed."},
        "coherence_and_cohesion": {"band": 6.5, "feedback": "Paragraphing is clear."},
        "lexical_resource": {"band": 7.5, "feedback": "Vocabulary is varied."},
        "grammatical_range_and_accuracy": {"band": 6.0, "feedback": "Some errors remain."},
        "general_feedback": "A relevant and generally clear response.",
        "strengths": ["Clear position"],
        "improvements": ["Check complex sentence punctuation"],
        "sample_answer": None,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def writing_app(tmp_path):
    config = AppConfig(
        base_dir=tmp_path,
        database_path=tmp_path / "state.db",
        writing_model="configured-writing-model",
    )
    database = Database(config.database_path)
    database.create_schema()
    return config, Repository(database)


def test_evaluates_rounds_band_and_persists_audit_record(writing_app):
    config, repository = writing_app
    provider = StubProvider(evaluation_payload())
    submission = WritingEvaluationRequest(
        task_type="task_2",
        question="Some people prefer cities. Discuss both views and give your opinion.",
        essay="Cities provide opportunities, while rural areas offer calm. " * 8,
    )

    response = WritingEvaluationService(provider, config, repository).evaluate(submission)

    assert response.overall_band == 7.0
    assert response.task_response.band == 7.0
    assert response.task_achievement is None
    assert provider.requests[0].model == "configured-writing-model"
    record = repository.get_writing_evaluation(response.id)
    assert record["request"]["essay"] == submission.essay
    assert record["response"]["overall_band"] == 7.0
    assert record["input_tokens"] == 100


def test_rejects_non_half_band_from_model(writing_app):
    config, repository = writing_app
    provider = StubProvider(evaluation_payload(task_fulfilment={"band": 6.7, "feedback": "x"}))
    submission = WritingEvaluationRequest(
        task_type="task_1",
        question="The chart shows changes in household energy use over time.",
        essay="The chart demonstrates several changes across the period shown.",
    )

    with pytest.raises(AgentSchemaError, match="writing_evaluation"):
        WritingEvaluationService(provider, config, repository).evaluate(submission)


def test_rejects_unexpected_model_fields(writing_app):
    config, repository = writing_app
    provider = StubProvider(evaluation_payload(overall_band=9.0))
    submission = WritingEvaluationRequest(
        task_type="task_2",
        question="Some people prefer cities. Discuss both views and give your opinion.",
        essay="Cities provide opportunities, while rural areas offer calm. " * 8,
    )

    with pytest.raises(AgentSchemaError, match="writing_evaluation"):
        WritingEvaluationService(provider, config, repository).evaluate(submission)


def test_omits_unrequested_sample_answer(writing_app):
    config, repository = writing_app
    provider = StubProvider(evaluation_payload(sample_answer="A model response."))
    submission = WritingEvaluationRequest(
        task_type="task_1",
        question="The chart shows changes in household energy use over time.",
        essay="The chart demonstrates several changes across the period shown.",
    )

    response = WritingEvaluationService(provider, config, repository).evaluate(submission)

    assert response.sample_answer is None


def test_ielts_quarter_band_rounds_up(writing_app):
    config, repository = writing_app
    provider = StubProvider(
        evaluation_payload(
            task_fulfilment={"band": 6.5, "feedback": "Relevant."},
            coherence_and_cohesion={"band": 6.5, "feedback": "Organised."},
            lexical_resource={"band": 6.0, "feedback": "Adequate."},
            grammatical_range_and_accuracy={"band": 6.0, "feedback": "Mostly clear."},
        )
    )
    submission = WritingEvaluationRequest(
        task_type="task_2",
        question="Some people prefer cities. Discuss both views and give your opinion.",
        essay="Cities provide opportunities, while rural areas offer calm. " * 8,
    )

    response = WritingEvaluationService(provider, config, repository).evaluate(submission)

    assert response.overall_band == 6.5