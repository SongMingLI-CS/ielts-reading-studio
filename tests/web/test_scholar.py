import httpx
from pydantic import SecretStr

from tests.web.conftest import bootstrap_csrf


def configure(web_service):
    web_service.config.scholar_kernel_url = "https://scholar.example"
    web_service.config.scholar_kernel_api_url = "http://127.0.0.1:3001"
    web_service.config.scholar_kernel_bridge_token = SecretStr("bridge-test-secret")


def payload():
    return {"request_id": "81520f50-8e6e-40a9-9a5b-e7cb84964914", "title": "NumPy", "text": "An ndarray is a multidimensional array.", "question": "解释 shape 和 dtype", "source_path": "/knowledge?view=terms"}


def test_scholar_is_disabled_until_configured(client):
    assert client.post("/api/scholar/import", json=payload()).status_code == 503


def test_scholar_transfers_context_and_returns_only_a_safe_launch_url(client, web_service, monkeypatch):
    configure(web_service)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(201, request=httpx.Request("POST", "http://scholar.test"), json={"conversationId": "learning-" + payload()["request_id"]})

    monkeypatch.setattr("app.web.routes_scholar.httpx.post", post)
    response = client.post("/api/scholar/import", json=payload())
    assert response.status_code == 201
    assert response.json()["url"].startswith("https://scholar.example/?conversation=learning-")
    assert "learning=1" in response.json()["url"]
    assert "bridge-test-secret" not in response.text
    assert calls[0][0] == "http://127.0.0.1:3001/api/integrations/learning-studio"
    assert calls[0][1]["json"]["text"] == payload()["text"]
    assert calls[0][1]["json"]["sourceUrl"].endswith(payload()["source_path"])
    assert calls[0][1]["headers"]["Authorization"] == "Bearer bridge-test-secret"


def test_scholar_rejects_bad_context_and_unsafe_upstream_url(client, web_service, monkeypatch):
    configure(web_service)
    assert client.post("/api/scholar/import", json={**payload(), "text": ""}).status_code == 422
    assert client.post("/api/scholar/import", json={**payload(), "text": "x" * 12001}).status_code == 422
    assert client.post("/api/scholar/import", json={**payload(), "source_path": "//evil.example"}).status_code == 422
    monkeypatch.setattr("app.web.routes_scholar.httpx.post", lambda *a, **kw: httpx.Response(201, request=httpx.Request("POST", "http://scholar.test"), json={"conversationId": "//evil.example"}))
    assert client.post("/api/scholar/import", json=payload()).status_code == 502


def test_scholar_timeout_is_recoverable_and_csrf_is_required(client, raw_client, web_service, monkeypatch):
    configure(web_service)
    assert raw_client.post("/api/scholar/import", json=payload()).status_code == 403

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("upstream-secret")

    monkeypatch.setattr("app.web.routes_scholar.httpx.post", timeout)
    response = client.post("/api/scholar/import", json=payload())
    assert response.status_code == 502
    assert "upstream-secret" not in response.text


def test_scholar_widget_is_available_on_reading_and_knowledge_pages(client, web_service, completed_unit):
    configure(web_service)
    bootstrap_csrf(client)
    for path in ("/knowledge", f"/practice/{completed_unit.id}/compare", "/novel"):
        page = client.get(path)
        assert 'id="scholar-dialog"' in page.text
        assert "bridge-test-secret" not in page.text
