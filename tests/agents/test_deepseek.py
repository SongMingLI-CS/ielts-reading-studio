from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

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


@dataclass
class FakeHTTPError(Exception):
    status_code: int
    message: str = "provider error"

    def __str__(self) -> str:
        return self.message


class FakeCompletions:
    def __init__(self):
        self.outcomes: list[object] = []
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self):
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def last_request(self) -> dict[str, object]:
        return self.completions.requests[-1]

    def reply(
        self,
        content: str | None,
        *,
        finish_reason: str = "stop",
        prompt_tokens: int = 12,
        completion_tokens: int = 5,
    ) -> None:
        self.completions.outcomes.append(
            SimpleNamespace(
                id="response-1",
                model="deepseek-test",
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content=content),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
            )
        )


@pytest.fixture
def config(tmp_path):
    return AppConfig(base_dir=tmp_path, deepseek_api_key="sk-1234567890abcdef")


@pytest.fixture
def fake_client():
    return FakeClient()


def request() -> ModelRequest:
    return ModelRequest(
        stage="author_brief",
        model="deepseek-test",
        system='Return one JSON object such as {"ok": true}.',
        user='Use this JSON input: {"source": "text"}.',
        max_tokens=100,
        temperature=0.2,
    )


def test_requests_json_mode_and_records_usage(fake_client, config):
    fake_client.reply('{"ok":true}', prompt_tokens=12, completion_tokens=5)

    result = DeepSeekProvider(config, client=fake_client).complete_json(request())

    assert result.payload == {"ok": True}
    assert result.input_tokens == 12
    assert result.output_tokens == 5
    assert result.retries == 0
    assert fake_client.last_request["response_format"] == {"type": "json_object"}
    assert fake_client.last_request["max_tokens"] == 100
    assert fake_client.last_request["temperature"] == 0.2
    assert "frequency_penalty" not in fake_client.last_request
    assert "presence_penalty" not in fake_client.last_request


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_json_response_is_classified(content, fake_client, config):
    fake_client.reply(content)
    with pytest.raises(EmptyResponseError) as caught:
        DeepSeekProvider(config, client=fake_client).complete_json(request())
    assert caught.value.retryable is True


def test_rejects_truncated_response_before_json_parsing(fake_client, config):
    fake_client.reply('{"partial":', finish_reason="length")
    with pytest.raises(TruncatedResponseError):
        DeepSeekProvider(config, client=fake_client).complete_json(request())


@pytest.mark.parametrize("content", ["not json", "[]", "42"])
def test_rejects_invalid_or_non_object_json(content, fake_client, config):
    fake_client.reply(content)
    with pytest.raises(InvalidResponseError):
        DeepSeekProvider(config, client=fake_client).complete_json(request())


def test_retries_rate_limit_with_exponential_jitterless_delays(fake_client, config):
    fake_client.completions.outcomes.extend([FakeHTTPError(429), FakeHTTPError(429)])
    fake_client.reply('{"ok": true}')
    sleeps: list[float] = []

    result = DeepSeekProvider(
        config,
        client=fake_client,
        sleep=sleeps.append,
        jitter=lambda: 0.0,
    ).complete_json(request())

    assert result.retries == 2
    assert sleeps == [1.0, 2.0]
    assert len(fake_client.completions.requests) == 3


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ProviderAuthError), (402, ProviderBillingError)],
)
def test_fatal_provider_errors_are_never_retried(status, error_type, fake_client, config):
    fake_client.completions.outcomes.append(
        FakeHTTPError(status, "Bearer sk-1234567890abcdef rejected")
    )
    sleeps: list[float] = []

    with pytest.raises(error_type) as caught:
        DeepSeekProvider(config, client=fake_client, sleep=sleeps.append).complete_json(request())

    assert "sk-1234567890abcdef" not in str(caught.value)
    assert sleeps == []
    assert len(fake_client.completions.requests) == 1


def test_rate_limit_exhaustion_uses_three_bounded_retries(fake_client, config):
    fake_client.completions.outcomes.extend([FakeHTTPError(429) for _ in range(4)])
    sleeps: list[float] = []

    with pytest.raises(ProviderRateLimitError) as caught:
        DeepSeekProvider(
            config,
            client=fake_client,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
        ).complete_json(request())

    assert caught.value.retries == 3
    assert sleeps == [1.0, 2.0, 4.0]


def test_server_error_is_classified_after_retry_exhaustion(fake_client, config):
    fake_client.completions.outcomes.extend([FakeHTTPError(503) for _ in range(4)])

    with pytest.raises(ProviderServerError):
        DeepSeekProvider(config, client=fake_client, sleep=lambda _: None).complete_json(request())


def test_request_must_explicitly_ask_for_json(fake_client, config):
    bad = request().model_copy(update={"system": "Return structured data.", "user": "source"})
    with pytest.raises(ValueError, match="JSON"):
        DeepSeekProvider(config, client=fake_client).complete_json(bad)
    assert fake_client.completions.requests == []
