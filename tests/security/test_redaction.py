"""Credential redaction in logs and error surfaces."""

from __future__ import annotations

import logging

import pytest

from app.config import redact_secrets
from app.security.redaction import REDACTED, redact_headers, redact_text, summarize
from app.storage.repositories import Repository


def test_redact_text_scrubs_headers_tokens_and_credentials():
    parts = [
        "Authorization: Basic cmVhZGVyOnNlY3JldA==",
        "Cookie: ielts_session=deadbeefdeadbeef",
        "X-CSRF-Token: 0123456789abcdef0123456789abcdef",
        "api_key=sk-abcdefghijklmnopqrstuvwxyz",
        "DEEPSEEK_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
        "IELTS_WEB_PASSWORD=hunter2",
        "IELTS_WEB_SESSION_SECRET=averylongsessionsecretvalue",
        "postgres://user:pa55word@db.internal/app",
        "password='hunter2'",
    ]
    sample = " ".join(parts)

    cleaned = redact_text(sample)

    for leaked in (
        "cmVhZGVyOnNlY3JldA==",
        "deadbeefdeadbeef",
        "0123456789abcdef",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "hunter2",
        "averylongsessionsecretvalue",
        "pa55word",
    ):
        assert leaked not in cleaned, f"{leaked} 未被脱敏"
    assert REDACTED in cleaned


def test_redact_text_keeps_diagnostics_readable():
    """Checksums and ordinary identifiers must survive scrubbing."""

    text = "corpus=3f786850e387550fdab836ed7e6dc881de23001b unit=1234 elapsed=12ms"

    assert redact_text(text) == text
    assert redact_secrets(text) == text


def test_redact_headers_only_hides_credential_headers():
    headers = {
        "Authorization": "Basic abc",
        "Cookie": "ielts_session=x",
        "Set-Cookie": "ielts_session=x; HttpOnly",
        "Content-Type": "text/html",
        "Host": "example.test",
    }

    cleaned = redact_headers(headers)

    assert cleaned["Authorization"] == REDACTED
    assert cleaned["Cookie"] == REDACTED
    assert cleaned["Set-Cookie"] == REDACTED
    assert cleaned["Content-Type"] == "text/html"
    assert cleaned["Host"] == "example.test"


def test_summarize_never_returns_the_payload():
    summary = summarize("private essay text", limit=5)

    assert "private essay text" not in summary
    assert "len=18" in summary
    assert "preview='priva'" in summary


def test_csrf_rejection_logs_do_not_contain_tokens(raw_client, caplog):
    caplog.set_level(logging.INFO)
    token = "0123456789abcdef0123456789abcdef"

    raw_client.post(
        "/practice/vocabulary/mark", data={"word": "x"}, headers={"X-CSRF-Token": token}
    )

    assert token not in caplog.text
    assert "csrf_invalid_token" in caplog.text or "CSRF" in caplog.text


def test_session_cookie_is_never_logged(raw_client, caplog):
    caplog.set_level(logging.DEBUG)

    raw_client.get("/")
    session_id = raw_client.cookies.get("ielts_session")
    raw_client.post("/practice/vocabulary/mark", data={"word": "x"})

    assert session_id
    assert session_id not in caplog.text


def test_login_failure_does_not_echo_the_password(web_service):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from app.pipeline.service import ReadingStudioService
    from app.web.app import create_app

    service = ReadingStudioService(
        web_service.config.model_copy(
            update={
                "web_username": "reader",
                "web_password": SecretStr("a-long-enough-password"),
            }
        )
    )
    with TestClient(create_app(config=service.config, service=service)) as client:
        response = client.get("/practice", auth=("reader", "super-secret-typo"))

    assert response.status_code == 401
    assert "super-secret-typo" not in response.text
    assert "a-long-enough-password" not in response.text
    # No traceback, path or header echo for unauthenticated callers.
    assert "Traceback" not in response.text
    assert "/Users/" not in response.text


def test_unknown_exception_returns_a_generic_body(web_service, monkeypatch):
    from fastapi.testclient import TestClient

    from app.web.app import create_app

    def explode(*_args, **_kwargs):
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(Repository, "get_corpus", explode)
    with TestClient(
        create_app(config=web_service.config, service=web_service)
    ) as client:
        response = client.get("/corpora/some-id/preview")

    assert response.status_code == 500
    body = response.text
    assert response.json()["code"] == "internal_error"
    for leaked in ("internal detail", "Traceback", "get_corpus", "/Users/"):
        assert leaked not in body
    assert "X-Content-Type-Options" in response.headers


@pytest.mark.parametrize(
    "raw, expected_missing",
    [
        ("Bearer abc123", "abc123"),
        ("sk-abcdefghijklmnop", "sk-abcdefghijklmnop"),
    ],
)
def test_secret_shapes_are_covered(raw, expected_missing):
    assert expected_missing not in redact_text(raw)
