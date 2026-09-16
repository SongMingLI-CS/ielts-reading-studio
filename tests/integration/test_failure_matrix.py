from __future__ import annotations

import json

import pytest

from app.agents.base import (
    EmptyResponseError,
    InvalidResponseError,
    ModelRequest,
    ProviderAuthError,
    ProviderBillingError,
    ProviderRateLimitError,
    ProviderServerError,
    TruncatedResponseError,
)
from app.agents.deepseek import DeepSeekProvider
from app.config import AppConfig
from app.models import Corpus, Difficulty, GenerationUnit, UnitStatus
from app.pipeline.unit_runner import UnitRunner
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository
from tests.agents.test_deepseek import FakeClient, FakeHTTPError
from tests.fixtures.valid_generation import valid_assessment_payload
from tests.pipeline.conftest import FakeExaminer, PassValidator


def request() -> ModelRequest:
    return ModelRequest(
        stage="failure_matrix",
        model="fake",
        system='Return JSON such as {"ok": true}.',
        user='JSON input: {"test": true}',
        max_tokens=100,
    )


@pytest.mark.parametrize(
    ("outcome", "error_type", "expected_retries"),
    [
        (FakeHTTPError(401), ProviderAuthError, 0),
        (FakeHTTPError(402), ProviderBillingError, 0),
        (FakeHTTPError(429), ProviderRateLimitError, 3),
        (FakeHTTPError(500), ProviderServerError, 3),
        (TimeoutError("timeout"), ProviderServerError, 3),
    ],
)
def test_transport_failure_matrix(tmp_path, outcome, error_type, expected_retries):
    client = FakeClient()
    copies = expected_retries + 1
    client.completions.outcomes.extend([outcome for _ in range(copies)])
    provider = DeepSeekProvider(
        AppConfig(base_dir=tmp_path, deepseek_api_key="sk-fake"),
        client=client,
        sleep=lambda _delay: None,
        jitter=lambda: 0.0,
    )
    with pytest.raises(error_type) as caught:
        provider.complete_json(request())
    assert caught.value.retries == expected_retries
    assert len(client.completions.requests) == copies


@pytest.mark.parametrize(
    ("content", "finish_reason", "error_type"),
    [
        ("", "stop", EmptyResponseError),
        ('{"partial":', "length", TruncatedResponseError),
        ("bad json", "stop", InvalidResponseError),
    ],
)
def test_content_failure_matrix(tmp_path, content, finish_reason, error_type):
    client = FakeClient()
    client.reply(content, finish_reason=finish_reason)
    provider = DeepSeekProvider(
        AppConfig(base_dir=tmp_path, deepseek_api_key="sk-fake"),
        client=client,
    )
    with pytest.raises(error_type):
        provider.complete_json(request())


@pytest.mark.parametrize(
    "error",
    [
        ProviderAuthError("auth", retries=0),
        ProviderBillingError("billing", retries=0),
        ProviderRateLimitError("rate", retries=3),
        ProviderServerError("server", retries=3),
    ],
)
def test_provider_failure_sets_exact_unit_state_and_saved_error(tmp_path, error):
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
        id="unit-1",
        corpus_id="corpus-1",
        source_chapter_ids=["chapter-1"],
        source_text_hash="source-hash",
        difficulty=Difficulty.STANDARD,
        question_types=[group["type"] for group in valid_assessment_payload()["question_groups"]],
        config_snapshot={},
        prompt_version="1",
        status=UnitStatus.INDEXED,
    )
    repository.add_unit(unit)
    store = ArtifactStore(config.output_dir)

    class FailingAuthor:
        last_result = None

        def create_brief(self, unit, source_text):
            raise error

    runner = UnitRunner(
        config=config,
        repository=repository,
        store=store,
        author=FailingAuthor(),
        examiner=FakeExaminer(),
        source_text_loader=lambda _unit: "原文",
        passage_validator=PassValidator(),
        question_validator=PassValidator(),
    )

    with pytest.raises(type(error)):
        runner.run(unit.id)

    assert repository.get_unit(unit.id).status == UnitStatus.FAILED
    attempt = repository.get_stage_attempt(unit.id, "author_brief", 1)
    assert attempt.status == "failed"
    usage = repository.list_usage_records(unit.id)
    assert usage[-1].error_type == error.code
    assert usage[-1].retries == error.retries
    failure = json.loads(store._destination(f"failed/{unit.id}.json").read_text(encoding="utf-8"))
    assert failure["code"] == error.code
    assert failure["issues"][0]["retries"] == error.retries
