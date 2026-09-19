"""Session cookie attributes, rotation, invalidation and production key validation."""

from __future__ import annotations

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import AppConfig
from app.pipeline.service import ReadingStudioService
from app.security.sessions import SESSION_COOKIE, SessionStore
from app.storage.database import Database, web_sessions
from app.web.app import create_app

FORGED = "f" * 64


def token_from_page(client: TestClient) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/").text)
    assert match, "页面必须渲染 CSRF meta 标签"
    return match.group(1)


def protected_service(web_service, **overrides) -> ReadingStudioService:
    config = web_service.config.model_copy(
        update={
            "web_username": "reader",
            "web_password": SecretStr("a-long-enough-password"),
            **overrides,
        }
    )
    return ReadingStudioService(config)


def test_session_cookie_is_httponly_lax_and_root_scoped(web_service):
    with TestClient(create_app(config=web_service.config, service=web_service)) as client:
        cookie = client.get("/").headers.get("set-cookie", "")

    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.casefold()
    assert "Path=/" in cookie
    # Plain HTTP local development must keep working, so Secure is not forced.
    assert "Secure" not in cookie


def test_secure_attribute_follows_configuration(web_service):
    service = ReadingStudioService(
        web_service.config.model_copy(update={"web_cookie_secure": True})
    )

    with TestClient(create_app(config=service.config, service=service)) as client:
        cookie = client.get("/").headers.get("set-cookie", "")

    assert "Secure" in cookie


def test_cookie_value_is_hashed_before_storage(web_service, raw_client):
    raw_client.get("/")
    session_id = raw_client.cookies.get(SESSION_COOKIE)

    with web_service.database.engine.connect() as connection:
        stored = [row[0] for row in connection.execute(web_sessions.select())]

    assert session_id
    assert stored, "会话应当写入数据库"
    assert session_id not in stored, "数据库不应保存明文会话 ID"


def test_login_rotates_the_session_id(web_service):
    """Session fixation defence: credentials on an anonymous session get a new id."""

    service = protected_service(web_service)
    credentials = ("reader", "a-long-enough-password")
    with TestClient(create_app(config=service.config, service=service)) as client:
        anonymous = client.get("/practice")
        first_id = client.cookies.get(SESSION_COOKIE)
        authenticated = client.get("/practice", auth=credentials)
        second_id = client.cookies.get(SESSION_COOKIE)

    assert anonymous.status_code == 401
    assert first_id, "未登录访问也应当拿到匿名会话"
    assert authenticated.status_code == 200
    assert second_id and second_id != first_id, "登录后必须轮换会话标识"


def test_forged_session_cookie_is_not_accepted(raw_client):
    raw_client.cookies.set(SESSION_COOKIE, FORGED)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve"},
        headers={"X-CSRF-Token": "0" * 64},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_no_session"


def test_session_store_rejects_missing_and_malformed_ids(web_service):
    database = Database(web_service.config.database_path)
    sessions = SessionStore(database, secret="s" * 32, idle_minutes=60, absolute_hours=24)

    assert sessions.load(None) is None
    assert sessions.load("short") is None
    assert sessions.load("0" * 64) is None


def test_logout_invalidates_the_session_server_side(web_service):
    with TestClient(create_app(config=web_service.config, service=web_service)) as client:
        token = token_from_page(client)
        session_id = client.cookies.get(SESSION_COOKIE)

        response = client.post(
            "/logout", headers={"X-CSRF-Token": token}, follow_redirects=False
        )

        assert response.status_code == 303
        assert client.cookies.get(SESSION_COOKIE) in {None, ""}
        with web_service.database.engine.connect() as connection:
            remaining = list(connection.execute(web_sessions.select()))
        # The row is gone, so replaying the old cookie value cannot resurrect it.
        client.cookies.set(SESSION_COOKIE, session_id or "")
        replayed = client.post(
            "/practice/vocabulary/mark",
            data={"word": "conserve"},
            headers={"X-CSRF-Token": token},
        )

    assert remaining == []
    assert replayed.status_code == 403
    assert replayed.json()["code"] == "csrf_no_session"


def expire_all_sessions(web_service) -> None:
    past = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(minutes=1)
    with web_service.database.engine.begin() as connection:
        connection.execute(
            web_sessions.update().values(idle_expires_at=past, absolute_expires_at=past)
        )


def test_expired_session_is_rejected_and_removed(web_service, raw_client):
    raw_client.get("/")
    session_id = raw_client.cookies.get(SESSION_COOKIE)
    expire_all_sessions(web_service)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve"},
        headers={"X-CSRF-Token": "0" * 64},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_no_session"
    store = raw_client.app.state.security.sessions
    assert store.load(session_id) is None


def test_absolute_expiry_also_invalidates(web_service, raw_client):
    raw_client.get("/")
    session_id = raw_client.cookies.get(SESSION_COOKIE)
    future = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(hours=1)
    past = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(minutes=1)
    with web_service.database.engine.begin() as connection:
        connection.execute(
            web_sessions.update().values(idle_expires_at=future, absolute_expires_at=past)
        )

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve"},
        headers={"X-CSRF-Token": "0" * 64},
    )

    assert response.status_code == 403
    assert raw_client.app.state.security.sessions.load(session_id) is None


def test_production_requires_a_strong_session_secret():
    config = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("a-long-enough-password"),
        web_force_https=True,
        web_trusted_proxies="127.0.0.1",
    )

    issues = config.production_issues(host="0.0.0.0")

    assert any("IELTS_WEB_SESSION_SECRET" in issue for issue in issues)


def test_production_rejects_placeholder_or_short_secrets():
    base = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("a-long-enough-password"),
        web_force_https=True,
        web_trusted_proxies="127.0.0.1",
        web_session_secret=SecretStr("development-only-session-key"),
    )

    assert any("示例值" in issue for issue in base.production_issues(host="0.0.0.0"))
    short = base.model_copy(update={"web_session_secret": SecretStr("tooshort")})
    assert any("32 个字符" in issue for issue in short.production_issues(host="0.0.0.0"))


def test_production_rejects_weak_password_and_missing_https():
    config = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("secret"),
        web_session_secret=SecretStr("x" * 40),
    )

    issues = config.production_issues(host="0.0.0.0")

    assert any("12 个字符" in issue for issue in issues)
    assert any("HTTPS" in issue for issue in issues)


def test_password_min_length_can_be_lowered_explicitly():
    """A deployment may relax the floor, but only as a visible configuration choice."""

    strict = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("0123456789"),
        web_force_https=True,
        web_trusted_proxies="127.0.0.1",
        web_session_secret=SecretStr("s" * 48),
    )

    assert any("12 个字符" in issue for issue in strict.production_issues(host="0.0.0.0"))

    relaxed = strict.model_copy(update={"web_password_min_length": 10})

    assert relaxed.production_issues(host="0.0.0.0") == []


def test_relaxed_password_min_length_still_uses_the_new_limit_and_the_weak_list():
    relaxed = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("short"),
        web_password_min_length=10,
        web_force_https=True,
        web_trusted_proxies="127.0.0.1",
        web_session_secret=SecretStr("s" * 48),
    )

    assert any("10 个字符" in issue for issue in relaxed.production_issues(host="0.0.0.0"))

    weak = relaxed.model_copy(update={"web_password": SecretStr("changeme")})
    assert any("弱口令" in issue for issue in weak.production_issues(host="0.0.0.0"))


def test_password_min_length_is_bounded_so_a_typo_cannot_disable_it():
    with pytest.raises(ValidationError):
        AppConfig(base_dir=".", web_password_min_length=4)
    with pytest.raises(ValidationError):
        AppConfig(base_dir=".", web_password_min_length=4096)


def test_production_accepts_a_complete_configuration():
    config = AppConfig(
        base_dir=".",
        web_username="reader",
        web_password=SecretStr("a-long-enough-password"),
        web_force_https=True,
        web_trusted_proxies="127.0.0.1,172.16.0.0/12",
        web_session_secret=SecretStr("s" * 48),
    )

    assert config.production_issues(host="0.0.0.0") == []
    assert config.cookies_secure() is True
    assert config.session_cookie_settings()["httponly"] is True
    assert config.session_cookie_settings()["samesite"] == "lax"

