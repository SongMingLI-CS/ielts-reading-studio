from __future__ import annotations

from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.agents.base import ModelResult, ProviderAuthError
from app.config import AppConfig
from app.pipeline.service import ReadingStudioService
from app.web.app import create_app
from tests.web.conftest import bootstrap_csrf


@pytest.fixture
def keyed_config(tmp_path):
    return AppConfig(
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
        deepseek_api_key="sk-super-secret-value",
    )


@pytest.fixture
def keyed_client(keyed_config):
    service = ReadingStudioService(keyed_config)
    with TestClient(create_app(config=keyed_config, service=service)) as value:
        bootstrap_csrf(value)
        yield value


def test_settings_page_explains_effective_config_without_leaking_secrets(keyed_client, keyed_config):
    response = keyed_client.get("/settings")

    assert response.status_code == 200
    page = response.text
    assert keyed_config.author_model in page
    assert keyed_config.examiner_model in page
    assert str(keyed_config.database_path) in page
    assert "出题模型" in page and "返工上限" in page and "切分与合并" in page
    assert "sk-super-secret-value" not in page
    assert "已配置" in page


def test_settings_page_reports_missing_key(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    page = client.get("/settings").text

    assert "未配置" in page
    assert "未在任何位置找到" in page


def test_settings_page_reports_where_the_key_comes_from(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-environment")

    page = client.get("/settings").text

    assert "已配置" in page
    assert "进程环境变量 DEEPSEEK_API_KEY" in page
    assert "sk-from-environment" not in page


def test_provider_probe_without_key_reports_actionable_status(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    response = client.post("/settings/test-provider", follow_redirects=False)

    assert response.status_code == 303
    location = unquote(response.headers["location"])
    assert "ok=0" in location
    assert "未配置密钥" in location
    assert "DEEPSEEK_API_KEY" in location


def test_provider_probe_reports_success_and_redacts_key(keyed_client, monkeypatch):
    class StubProvider:
        def __init__(self, config, *, max_retries=3):
            self.config = config

        def complete_json(self, request):
            assert request.model == keyed_client.app.state.service.config.author_model
            return ModelResult(payload={"ok": True}, raw_text="{}", input_tokens=7, output_tokens=3)

    monkeypatch.setattr("app.web.routes_settings.DeepSeekProvider", StubProvider)

    response = keyed_client.post("/settings/test-provider", follow_redirects=False)

    assert response.status_code == 303
    location = unquote(response.headers["location"]).replace("+", " ")
    assert "ok=1" in location
    assert "应答正常" in location
    assert '{"ok": true}' in location
    assert "用量 7" in location and "3 tokens" in location
    assert "sk-super-secret-value" not in location


def test_provider_probe_redacts_key_from_provider_errors(keyed_client, monkeypatch):
    class FailingProvider:
        def __init__(self, config, *, max_retries=3):
            self.config = config

        def complete_json(self, request):
            raise ProviderAuthError(
                f"Provider authentication failed: DEEPSEEK_API_KEY="
                f"{self.config.deepseek_api_key.get_secret_value()}"
            )

    monkeypatch.setattr("app.web.routes_settings.DeepSeekProvider", FailingProvider)

    response = keyed_client.post("/settings/test-provider", follow_redirects=False)

    location = unquote(response.headers["location"])
    assert "ok=0" in location
    assert "sk-super-secret-value" not in location
    assert "REDACTED" in location
