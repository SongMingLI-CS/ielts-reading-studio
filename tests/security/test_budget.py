"""Provider budgets: input caps, token clamping, timeouts, retries and job cost."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from app.agents.base import (
    ModelRequest,
    ProviderError,
    ProviderRateLimitError,
)
from app.agents.deepseek import DeepSeekProvider
from app.config import AppConfig
from app.models import Difficulty
from app.pipeline.service import ReadingStudioService
from app.planning.units import default_question_types
from app.security.budget import (
    BudgetError,
    check_batch_cost,
    check_prompt,
    check_text_length,
    clamp_max_tokens,
)
from app.writing.models import WritingEvaluationRequest, WritingTaskType
from tests.fixtures.valid_generation import DeterministicProvider


def make_config(tmp_path, **overrides) -> AppConfig:
    return AppConfig(
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
        **overrides,
    )


class RecordingClient:
    """Minimal OpenAI-shaped client that records the requests it received."""

    def __init__(self, *, error: Exception | None = None, times: int = 0) -> None:
        self.calls: list[dict] = []
        self.error = error
        self.remaining_errors = times
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None and self.remaining_errors > 0:
            self.remaining_errors -= 1
            raise self.error
        message = type("Message", (), {"content": "{}"})()
        choice = type("Choice", (), {"finish_reason": "stop", "message": message})()
        usage = type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()
        return type(
            "Response",
            (),
            {"choices": [choice], "usage": usage, "model": "stub", "id": "resp-1"},
        )()


def request_for(prompt: str = 'Return JSON {"ok": true}') -> ModelRequest:
    return ModelRequest(
        stage="test", model="deepseek-test", system="JSON only.", user=prompt, max_tokens=100
    )


def test_text_length_budget_reports_the_overflow():
    decision = check_text_length("x" * 10, limit=5, label="输入")

    assert decision.allowed is False
    assert decision.code == "input_too_large"
    assert "10" in decision.detail


def test_prompt_budget_and_token_clamp_boundaries():
    assert check_prompt("a" * 10, "b" * 10, max_chars=20).allowed is True
    assert check_prompt("a" * 10, "b" * 11, max_chars=20).allowed is False
    assert clamp_max_tokens(5, cap=10) == 5
    assert clamp_max_tokens(50, cap=10) == 10
    assert clamp_max_tokens(0, cap=10, floor=1) == 1
    with pytest.raises(ValueError):
        clamp_max_tokens(5, cap=1, floor=2)


def test_batch_cost_budget_rejects_only_over_budget_work():
    assert check_batch_cost(estimated_tokens=100, limit=100, units=3).allowed is True
    refusal = check_batch_cost(estimated_tokens=101, limit=100, units=3)
    assert refusal.allowed is False
    assert refusal.code == "budget_exceeded"


def test_provider_clamps_output_tokens_and_passes_them_on(tmp_path):
    config = make_config(tmp_path, max_output_tokens=777)
    client = RecordingClient()

    DeepSeekProvider(config, client=client).complete_json(
        request_for().model_copy(update={"max_tokens": 999_999})
    )

    assert client.calls[0]["max_tokens"] == 777


def test_provider_refuses_an_oversized_prompt_without_calling_out(tmp_path):
    config = make_config(tmp_path, max_prompt_chars=1_000)
    client = RecordingClient()

    with pytest.raises(BudgetError) as caught:
        DeepSeekProvider(config, client=client).complete_json(request_for("x" * 2_000))

    assert caught.value.code == "prompt_too_large"
    assert client.calls == [], "预算拒绝时不得调用 provider"


def test_retryable_transport_errors_are_retried_up_to_the_configured_limit(tmp_path):
    class TransportError(Exception):
        """Shaped like an SDK error: carries a status code the adapter classifies."""

        status_code = 429

    config = make_config(tmp_path)
    sleeps: list[float] = []
    client = RecordingClient(error=TransportError("slow down"), times=99)
    provider = DeepSeekProvider(
        config, client=client, sleep=sleeps.append, jitter=lambda: 0.0, max_retries=2
    )

    with pytest.raises(ProviderRateLimitError):
        provider.complete_json(request_for())

    assert len(client.calls) == 3, "1 次原始调用 + 2 次重试"
    assert len(sleeps) == 2, "重试之间必须有退避等待"


def test_non_retryable_transport_errors_are_not_retried(tmp_path):
    class BadRequestError(Exception):
        status_code = 400

    config = make_config(tmp_path)
    sleeps: list[float] = []
    client = RecordingClient(error=BadRequestError("malformed request"), times=99)
    provider = DeepSeekProvider(config, client=client, sleep=sleeps.append, max_retries=3)

    with pytest.raises(ProviderError):
        provider.complete_json(request_for())

    assert len(client.calls) == 1, "不可重试错误只允许一次调用"
    assert sleeps == []


def test_openai_client_uses_configured_timeouts(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    config = make_config(
        tmp_path,
        deepseek_api_key=SecretStr("sk-test-not-a-real-key"),
        provider_timeout_seconds=42.0,
        provider_max_retries=2,
    )

    provider = DeepSeekProvider(config)

    assert captured["timeout"] == 42.0
    assert captured["max_retries"] == 0, "SDK 重试必须关闭，重试次数由适配器统一控制"
    assert provider.max_retries == 2


def test_writing_evaluation_rejects_oversized_input_before_the_provider(tmp_path):
    provider = DeterministicProvider()
    service = ReadingStudioService(
        make_config(tmp_path, max_writing_chars=500), provider=provider
    )
    submission = WritingEvaluationRequest(
        task_type=WritingTaskType.TASK_2, question="Discuss both views.", essay="x " * 400
    )

    with pytest.raises(BudgetError) as caught:
        service.evaluate_writing(submission)

    assert caught.value.code == "input_too_large"
    assert provider.requests == [], "超长输入不得触发任何模型调用"


def test_batch_job_over_budget_is_refused_before_queueing(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text(
        "\n".join(f"第{index}章\n" + "正文。" * 200 for index in range(1, 6)),
        encoding="utf-8",
    )
    provider = DeterministicProvider()
    config = make_config(tmp_path)
    service = ReadingStudioService(config, provider=provider)
    corpus = service.import_source(source).corpus
    sample = service.generate_sample(
        corpus.id, Difficulty.STANDARD, default_question_types(Difficulty.STANDARD)
    )
    service.approve_sample(corpus.id, sample.unit_id)
    config.max_estimated_tokens_per_run = 1
    provider.requests.clear()

    with pytest.raises(BudgetError) as caught:
        service.create_job(corpus.id, ordinals=[2, 3])

    assert caught.value.code == "budget_exceeded"
    assert provider.requests == [], "预算拒绝不得产生任何模型调用"
    assert service.repository.list_jobs(corpus_id=corpus.id) == [], "被拒绝的批次不应入队"


def test_oversized_json_body_is_refused_before_parsing(raw_client):
    token = raw_client.get("/").headers.get("set-cookie", "")
    del token
    from tests.web.conftest import bootstrap_csrf

    payload = json.dumps(
        {"task_type": "task_2", "question": "q" * 10, "essay": "e" * 50}
    )
    response = raw_client.post(
        "/api/writing/evaluate",
        content=payload + " " * 2_000_000,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "body_too_large"
    assert bootstrap_csrf  # the shared fixture helper is imported for clarity

