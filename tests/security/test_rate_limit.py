"""Rate limiting: persistence, atomicity, buckets, retry hints and proxy trust."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.pipeline.service import ReadingStudioService
from app.security.client_ip import parse_trusted_proxies, resolve_client
from app.security.rate_limit import RateLimiter, RateLimitPolicy
from app.storage.database import Database
from app.web.app import create_app

PASSWORD = "a-long-enough-password"


class Clock:
    """Controllable clock so window tests never depend on wall time."""

    def __init__(self) -> None:
        self.now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += dt.timedelta(**kwargs)


def make_limiter(tmp_path) -> tuple[RateLimiter, Clock]:
    database = Database(tmp_path / "state.db")
    database.migrate()
    clock = Clock()
    return RateLimiter(database, clock=clock), clock


def test_limiter_allows_up_to_the_limit_then_refuses(tmp_path):
    limiter, _clock = make_limiter(tmp_path)
    policy = RateLimitPolicy(limit=2, window_seconds=60)

    first = limiter.hit("bucket", policy)
    second = limiter.hit("bucket", policy)
    third = limiter.hit("bucket", policy)

    assert (first.allowed, second.allowed, third.allowed) == (True, True, False)
    assert third.hits == 3
    assert 1 <= third.retry_after <= 60


def test_limiter_window_rolls_over(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    policy = RateLimitPolicy(limit=1, window_seconds=60)
    assert limiter.hit("bucket", policy).allowed is True
    assert limiter.hit("bucket", policy).allowed is False

    clock.advance(seconds=61)

    assert limiter.hit("bucket", policy).allowed is True


def test_limiter_counts_are_atomic_under_concurrency(tmp_path):
    limiter, _clock = make_limiter(tmp_path)
    policy = RateLimitPolicy(limit=100, window_seconds=60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _: limiter.hit("bucket", policy), range(40)))

    assert max(decision.hits for decision in decisions) == 40
    assert sum(decision.allowed for decision in decisions) == 40


def test_limiter_reset_and_purge(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    policy = RateLimitPolicy(limit=1, window_seconds=60)
    limiter.hit("bucket", policy)

    assert limiter.reset("bucket") == 1
    assert limiter.peek("bucket", policy).hits == 0

    limiter.hit("bucket", policy)
    clock.advance(minutes=5)
    assert limiter.purge_expired() == 1
    assert limiter.row_count() == 0


def test_policy_rejects_nonsense_values():
    with pytest.raises(ValueError):
        RateLimitPolicy(limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        RateLimitPolicy(limit=1, window_seconds=0)


# --------------------------------------------------------------------- client IP
def test_forwarded_header_is_ignored_without_trusted_proxies():
    resolved = resolve_client(peer="203.0.113.7", forwarded_for="1.2.3.4", trusted_proxies=())

    assert resolved.address == "203.0.113.7"
    assert resolved.source == "peer"


def test_forwarded_header_is_honoured_only_for_a_trusted_peer():
    trusted = parse_trusted_proxies("127.0.0.1,10.0.0.0/8")

    behind_proxy = resolve_client(
        peer="10.1.2.3", forwarded_for="198.51.100.9", trusted_proxies=trusted
    )
    direct = resolve_client(
        peer="203.0.113.7", forwarded_for="198.51.100.9", trusted_proxies=trusted
    )

    assert behind_proxy.address == "198.51.100.9"
    assert behind_proxy.source == "forwarded"
    assert direct.address == "203.0.113.7"


def test_malformed_trusted_proxy_configuration_is_rejected():
    with pytest.raises(ValueError):
        parse_trusted_proxies("not-an-ip")


def protected_service(web_service, **overrides) -> ReadingStudioService:
    return ReadingStudioService(
        web_service.config.model_copy(
            update={
                "web_username": "reader",
                "web_password": SecretStr(PASSWORD),
                **overrides,
            }
        )
    )


def test_spoofed_forwarded_for_cannot_escape_the_login_limit(web_service):
    """Without a trusted proxy the header is ignored, so buckets do not multiply."""

    service = protected_service(
        web_service, web_login_rate_limit=2, web_login_rate_window_seconds=300
    )
    with TestClient(create_app(config=service.config, service=service)) as client:
        statuses = [
            client.get(
                "/practice",
                auth=("reader", "wrong-password"),
                headers={"X-Forwarded-For": f"198.51.100.{index}"},
            ).status_code
            for index in range(4)
        ]

    assert statuses[:2] == [401, 401]
    assert 429 in statuses[2:], "伪造转发头不应绕过登录限速"


def test_login_limit_returns_retry_after_and_hides_account_existence(web_service):
    service = protected_service(
        web_service, web_login_rate_limit=1, web_login_rate_window_seconds=120
    )
    with TestClient(create_app(config=service.config, service=service)) as client:
        first = client.get("/practice", auth=("reader", "wrong-password"))
        blocked = client.get("/practice", auth=("reader", "wrong-password"))
        other_account = client.get("/practice", auth=("nobody", "wrong-password"))

    assert first.status_code == 401
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    # An unknown account gets the same answer as a wrong password.
    assert other_account.status_code == 429


def test_successful_login_clears_the_failure_counter(web_service):
    service = protected_service(
        web_service, web_login_rate_limit=3, web_login_rate_window_seconds=300
    )
    with TestClient(create_app(config=service.config, service=service)) as client:
        for _ in range(3):
            client.get("/practice", auth=("reader", "wrong-password"))
        limited = client.get("/practice", auth=("reader", "wrong-password"))
        allowed = client.get("/practice", auth=("reader", PASSWORD))

    assert limited.status_code == 429
    assert allowed.status_code == 200


def test_expensive_endpoints_are_rate_limited(web_service, monkeypatch):
    """Provider-costing endpoints sit in the task bucket."""

    from app.agents.base import ModelResult

    class StubProvider:
        def __init__(self, config, *, max_retries=None):
            self.config = config

        def complete_json(self, request):
            return ModelResult(payload={"ok": True}, raw_text="{}")

    service = protected_service(
        web_service,
        web_task_rate_limit=1,
        web_task_rate_window_seconds=60,
        deepseek_api_key=SecretStr("sk-test-not-a-real-key"),
    )
    monkeypatch.setattr("app.web.routes_settings.DeepSeekProvider", StubProvider)
    credentials = ("reader", PASSWORD)
    with TestClient(create_app(config=service.config, service=service)) as client:
        import re

        page = client.get("/", auth=credentials).text
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        first = client.post(
            "/settings/test-provider",
            auth=credentials,
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        second = client.post(
            "/settings/test-provider",
            auth=credentials,
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )

    assert first.status_code == 303
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["code"] == "rate_limited_task"


def test_rate_limiting_can_be_disabled_for_local_use(web_service):
    service = protected_service(web_service, web_login_rate_limit=1, web_rate_limit_enabled=False)
    with TestClient(create_app(config=service.config, service=service)) as client:
        statuses = {
            client.get("/practice", auth=("reader", "wrong-password")).status_code
            for _ in range(3)
        }

    assert statuses == {401}

