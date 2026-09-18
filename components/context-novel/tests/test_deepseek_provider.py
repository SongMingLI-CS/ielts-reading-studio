from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from ielts_novel.config import AppConfig
from ielts_novel.models import Chapter, Paragraph
from ielts_novel.providers.base import (
    EmptyResponseError,
    InvalidResponseError,
    ProviderAuthError,
    ProviderBillingError,
    ProviderRateLimitError,
)
from ielts_novel.providers.deepseek_provider import DeepSeekProvider


class ApiError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class FakeCompletions:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


def _response(content, finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


def _chapter():
    return Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="她试图掩饰紧张。")])


def _provider(events, sleeps=None):
    completions = FakeCompletions(events)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions), models=SimpleNamespace(list=lambda: SimpleNamespace(data=[])))
    config = AppConfig(deepseek_api_key="sk-test-secret", max_retries=2)
    sleep_log = sleeps if sleeps is not None else []
    return DeepSeekProvider(config, client=client, sleeper=sleep_log.append, jitter=lambda: 0), completions


def test_empty_response_is_never_success():
    provider, _ = _provider([_response(""), _response(""), _response("")])
    with pytest.raises(EmptyResponseError):
        provider.generate_chapter(_chapter(), [], [])


def test_complete_json_returns_payload_and_usage():
    provider, _calls = _provider([_response(json.dumps({"items": [{"word": "conceal"}]}, ensure_ascii=False))])
    payload, input_tokens, output_tokens = provider.complete_json(system="s", user="u", max_tokens=2048)
    assert payload["items"][0]["word"] == "conceal"
    assert (input_tokens, output_tokens) == (10, 20)


def test_complete_json_rejects_non_object_json():
    provider, _calls = _provider([_response("[1, 2, 3]")])
    with pytest.raises(InvalidResponseError):
        provider.complete_json(system="s", user="u")


def test_complete_json_retries_empty_response_then_succeeds():
    sleeps: list[float] = []
    provider, _calls = _provider([_response(""), _response(json.dumps({"ok": True}))], sleeps=sleeps)
    payload, _in, _out = provider.complete_json(system="s", user="u")
    assert payload == {"ok": True}
    assert sleeps


def test_sdk_connection_errors_are_retried_then_succeed():
    class APIConnectionError(Exception):
        """Mimics the OpenAI SDK's transport error, which is not a TimeoutError."""

    sleeps: list[float] = []
    provider, _calls = _provider([APIConnectionError("connection reset"), _response(json.dumps({"ok": True}))], sleeps=sleeps)
    payload, _in, _out = provider.complete_json(system="s", user="u")
    assert payload == {"ok": True}
    assert sleeps


def test_generate_chapter_retries_transport_error():
    class APITimeoutError(Exception):
        """Mimics the OpenAI SDK timeout, which is not a TimeoutError subclass."""

    payload = {"chapter_id": 1, "chapter_title": "第一章", "paragraphs": [{"id": "1-001", "converted_text": "她试图 conceal（掩饰）紧张。", "inserted_terms": []}]}
    sleeps: list[float] = []
    provider, _calls = _provider([APITimeoutError("read timeout"), _response(json.dumps(payload, ensure_ascii=False))], sleeps=sleeps)

    result = provider.generate_chapter(_chapter(), [], [])

    assert result.chapter.chapter_id == 1
    assert sleeps
    payload = {"chapter_id": 1, "chapter_title": "第一章", "paragraphs": [{"id": "1-001", "converted_text": "她试图 conceal（掩饰）紧张。", "inserted_terms": [{"word": "conceal", "lemma": "conceal", "meaning": "掩饰", "part_of_speech": "verb", "cefr": "B2"}]}]}
    provider, calls = _provider([_response(json.dumps(payload, ensure_ascii=False))])
    result = provider.generate_chapter(_chapter(), [], [])
    assert result.chapter.chapter_id == 1
    assert result.input_tokens == 10 and result.output_tokens == 20
    assert calls.calls[0]["response_format"] == {"type": "json_object"}
    assert calls.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "JSON" in calls.calls[0]["messages"][0]["content"]
    assert '"minimum_learning_items"' in calls.calls[0]["messages"][1]["content"]
    assert '"target_learning_items"' in calls.calls[0]["messages"][1]["content"]
    assert '"target_items"' in calls.calls[0]["messages"][1]["content"]
    assert "原样复制原文将被判定为失败" in calls.calls[0]["messages"][0]["content"]


def test_429_uses_exponential_backoff_then_succeeds():
    sleeps = []
    payload = {"chapter_id": 1, "chapter_title": "第一章", "paragraphs": [{"id": "1-001", "converted_text": "文本", "inserted_terms": []}]}
    provider, _ = _provider([ApiError(429), _response(json.dumps(payload, ensure_ascii=False))], sleeps)
    provider.generate_chapter(_chapter(), [], [])
    assert sleeps == [2]


def test_401_stops_without_retry():
    provider, calls = _provider([ApiError(401)])
    with pytest.raises(ProviderAuthError):
        provider.generate_chapter(_chapter(), [], [])
    assert len(calls.calls) == 1


def test_rate_limit_exhaustion_has_safe_error():
    provider, _ = _provider([ApiError(429), ApiError(429), ApiError(429)])
    with pytest.raises(ProviderRateLimitError) as caught:
        provider.generate_chapter(_chapter(), [], [])
    assert "sk-test-secret" not in str(caught.value)


def test_secret_never_appears_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    provider, _ = _provider([ApiError(500), ApiError(500), ApiError(500)])
    # Three 500s exhaust max_retries, so the provider re-raises the final SDK error unchanged.
    with pytest.raises(ApiError):
        provider.generate_chapter(_chapter(), [], [])
    assert "sk-test-secret" not in caplog.text


def test_network_timeout_retries_then_succeeds():
    sleeps = []
    payload = {"chapter_id": 1, "chapter_title": "第一章", "paragraphs": [{"id": "1-001", "converted_text": "文本", "inserted_terms": []}]}
    provider, _ = _provider([TimeoutError("network timeout"), _response(json.dumps(payload, ensure_ascii=False))], sleeps)
    provider.generate_chapter(_chapter(), [], [])
    assert sleeps == [2]


def test_invalid_json_retries_and_never_marks_bad_content_successful():
    provider, _ = _provider([_response("not-json"), _response("not-json"), _response("not-json")])
    with pytest.raises(InvalidResponseError):
        provider.generate_chapter(_chapter(), [], [])


def test_402_stops_immediately_as_billing_error():
    provider, calls = _provider([ApiError(402)])
    with pytest.raises(ProviderBillingError):
        provider.generate_chapter(_chapter(), [], [])
    assert len(calls.calls) == 1
