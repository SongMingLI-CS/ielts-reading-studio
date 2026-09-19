"""CSRF protection: token sources, rejection codes and route coverage."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.models import Corpus
from app.web.app import create_app
from tests.web.conftest import bootstrap_csrf


def token_from_page(client: TestClient) -> str:
    page = client.get("/")
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match, "页面必须渲染 CSRF meta 标签"
    return match.group(1)


def test_unsafe_request_without_token_is_rejected(raw_client):
    response = raw_client.post("/practice/vocabulary/mark", data={"word": "conserve"})

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_missing_token"


def test_form_request_with_hidden_field_succeeds(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"csrf_token": token, "word": "conserve", "action": "save"},
    )

    assert response.status_code in {200, 303}


def test_form_request_with_header_succeeds(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve", "action": "save"},
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code in {200, 303}


def test_json_request_accepts_token_in_the_header(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/api/writing/evaluate",
        json={"task_type": "task_2", "question": "Discuss both views.", "essay": "x " * 40},
        headers={"X-CSRF-Token": token},
    )

    # No provider is configured here, so the request reaches the handler and fails there
    # (502) instead of being rejected by CSRF (403).
    assert response.status_code == 502


def test_json_request_accepts_token_in_the_body(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/api/writing/evaluate",
        json={
            "csrf_token": token,
            "task_type": "task_2",
            "question": "Discuss both views.",
            "essay": "x " * 40,
        },
    )

    assert response.status_code == 502


def test_wrong_token_is_rejected(raw_client):
    token_from_page(raw_client)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"csrf_token": "0" * 64, "word": "conserve"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid_token"


def test_token_from_another_session_is_rejected(web_service):
    """A token is bound to one session, so a copied token alone is useless."""

    with TestClient(create_app(config=web_service.config, service=web_service)) as first:
        foreign_token = token_from_page(first)
    with TestClient(create_app(config=web_service.config, service=web_service)) as second:
        second_token = token_from_page(second)

        response = second.post(
            "/practice/vocabulary/mark",
            data={"word": "conserve"},
            headers={"X-CSRF-Token": foreign_token},
        )

    assert foreign_token != second_token
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid_token"


def test_cross_site_origin_is_rejected_even_with_a_valid_token(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve"},
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_bad_origin"


def test_same_site_origin_is_accepted(raw_client):
    token = token_from_page(raw_client)

    response = raw_client.post(
        "/practice/vocabulary/mark",
        data={"word": "conserve"},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )

    assert response.status_code in {200, 303}


def test_read_only_requests_are_unaffected(raw_client):
    for path in ("/", "/practice", "/settings"):
        assert raw_client.get(path).status_code == 200


def test_polling_endpoint_stays_a_read_only_get(web_service, raw_client):
    """The job page polls a GET; CSRF must never block it (this app has no SSE)."""

    web_service.repository.add_corpus(
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
    web_service.repository.create_job("job-1", "corpus-1", "queued", {"unit_ids": []})

    response = raw_client.get("/jobs/job-1/units")

    assert response.status_code == 200
    assert response.json()["job_status"] == "queued"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/corpora/import"),
        ("POST", "/corpora/x/rebind"),
        ("POST", "/corpora/x/delete"),
        ("POST", "/corpora/x/boundaries"),
        ("POST", "/corpora/x/jobs"),
        ("POST", "/corpora/x/sample"),
        ("POST", "/corpora/x/approve-sample"),
        ("POST", "/jobs/x/pause"),
        ("POST", "/jobs/x/resume"),
        ("POST", "/jobs/x/retry"),
        ("POST", "/jobs/x/cancel"),
        ("POST", "/novel/import"),
        ("POST", "/novel/estimate"),
        ("POST", "/novel/generate-sample"),
        ("POST", "/novel/generate-batch"),
        ("POST", "/novel/recover/resume"),
        ("POST", "/exports"),
        ("POST", "/review/sample"),
        ("POST", "/review/x/decide"),
        ("POST", "/practice/vocabulary/mark"),
        ("POST", "/settings/test-provider"),
        ("POST", "/api/writing/evaluate"),
        ("POST", "/logout"),
    ],
)
def test_every_unsafe_route_requires_a_token(raw_client, method, path):
    """Coverage check: the middleware runs before routing, so no route can opt out."""

    response = raw_client.request(method, path)

    assert response.status_code == 403, f"{method} {path} must be CSRF protected"
    assert response.json()["code"].startswith("csrf_")


def test_registered_unsafe_routes_accept_a_valid_token(web_service):
    """A valid token must pass CSRF on every unsafe route (no hidden exemptions)."""

    application = create_app(config=web_service.config, service=web_service)
    unsafe = [
        route
        for route in application.routes
        if getattr(route, "methods", None)
        and {"POST", "PUT", "PATCH", "DELETE"} & set(route.methods)
    ]

    assert unsafe, "应用应当存在状态变更路由"
    with TestClient(application) as client:
        token = token_from_page(client)
        for route in unsafe:
            path = re.sub(r"\{[^}]+\}", "sample-id", route.path)
            response = client.post(path, headers={"X-CSRF-Token": token})
            if response.status_code == 403:
                code = str(response.json().get("code", ""))
                assert not code.startswith("csrf_"), f"{route.path} 拒绝了合法令牌"


def test_bootstrap_helper_returns_a_usable_token(web_service):
    with TestClient(create_app(config=web_service.config, service=web_service)) as client:
        token = bootstrap_csrf(client)

        assert len(token) == 64
        assert client.headers["X-CSRF-Token"] == token
