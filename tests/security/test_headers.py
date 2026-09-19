"""Security response headers, CSP nonce wiring and cache policy."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.pipeline.service import ReadingStudioService
from app.security.headers import content_security_policy, is_cacheable, new_nonce
from app.web.app import create_app

EXPECTED_HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "x-frame-options",
)


def test_private_pages_carry_every_security_header(raw_client):
    response = raw_client.get("/")

    for header in EXPECTED_HEADERS:
        assert response.headers.get(header), f"缺少安全响应头 {header}"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "Referrer-Policy" in response.headers


def test_error_responses_carry_the_same_headers(raw_client):
    response = raw_client.post("/practice/vocabulary/mark", data={"word": "x"})

    assert response.status_code == 403
    for header in EXPECTED_HEADERS:
        assert response.headers.get(header), f"错误响应缺少 {header}"
    assert "no-store" in response.headers["cache-control"]


def test_private_pages_are_never_cached(raw_client):
    for path in ("/", "/practice", "/settings", "/backup"):
        response = raw_client.get(path)
        assert response.status_code in {200, 404}
        assert "no-store" in response.headers.get("cache-control", ""), path
        assert response.headers.get("pragma") == "no-cache"


def test_static_assets_are_cacheable(raw_client):
    response = raw_client.get("/static/app.css")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get("cache-control", "")
    assert is_cacheable("/static/app.css") is True
    assert is_cacheable("/practice") is False


def test_csp_allows_only_nonce_scripts_and_same_origin_assets(raw_client):
    response = raw_client.get("/")
    policy = response.headers["content-security-policy"]

    assert "script-src 'self' 'nonce-" in policy
    assert "unsafe-eval" not in policy
    assert "*" not in policy.replace("'nonce-", "").replace("unsafe-inline", "")
    assert "style-src 'self' 'unsafe-inline'" in policy, "模板使用了 style 属性"
    assert "connect-src 'self'" in policy


def test_inline_scripts_use_the_nonce_from_the_header(raw_client):
    response = raw_client.get("/jobs/x")
    nonce = re.search(r"'nonce-([^']+)'", response.headers["content-security-policy"]).group(1)  # type: ignore[union-attr]

    assert nonce
    # Any inline script rendered on a page must carry that exact nonce.
    page = raw_client.get("/")
    for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", page.text):
        assert f'nonce="{nonce}"' in match.group(0), match.group(0)


def test_hsts_is_only_sent_over_https(web_service):
    service = ReadingStudioService(
        web_service.config.model_copy(
            update={
                "web_force_https": True,
                "web_trusted_proxies": "127.0.0.0/8,::1",
                "web_cookie_secure": True,
            }
        )
    )

    with TestClient(create_app(config=service.config, service=service)) as http_client:
        over_http = http_client.get("/healthz")
    with TestClient(
        create_app(config=service.config, service=service), base_url="https://testserver"
    ) as https_client:
        over_https = https_client.get("/healthz")

    assert "strict-transport-security" not in over_http.headers
    assert "max-age=31536000" in over_https.headers["strict-transport-security"]


def test_plain_http_public_deployment_is_refused(web_service):
    service = ReadingStudioService(
        web_service.config.model_copy(
            update={
                "web_force_https": True,
                "web_trusted_proxies": "127.0.0.0/8",
            }
        )
    )

    with TestClient(create_app(config=service.config, service=service)) as client:
        response = client.get("/practice")

    assert response.status_code == 403
    assert response.json()["code"] == "https_required"


def test_healthz_stays_minimal_and_public(raw_client):
    response = raw_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-store" in response.headers["cache-control"]


def test_nonce_is_unique_per_response(raw_client):
    first = re.search(r"'nonce-([^']+)'", raw_client.get("/").headers["content-security-policy"])
    second = re.search(r"'nonce-([^']+)'", raw_client.get("/").headers["content-security-policy"])

    assert first and second
    assert first.group(1) != second.group(1)
    assert new_nonce() != new_nonce()
    assert "unsafe-inline" not in content_security_policy(nonce="abc").split("style-src")[0]
