from __future__ import annotations

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
from tests.agents.test_deepseek import FakeClient, FakeHTTPError


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
