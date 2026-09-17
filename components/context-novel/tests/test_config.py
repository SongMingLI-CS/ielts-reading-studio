from __future__ import annotations

import logging

import pytest

from ielts_novel.config import AppConfig, ConfigurationError, redact_secrets


def test_loads_defaults_and_yaml_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("concurrency: 2\nmax_retries: 5\n", encoding="utf-8")

    config = AppConfig.load(config_file)

    assert config.concurrency == 2
    assert config.max_retries == 5
    assert config.deepseek_model == "deepseek-flash"
    assert config.deepseek_api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(config)


def test_missing_api_key_allowed_for_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text("concurrency: 1\n", encoding="utf-8")

    config = AppConfig.load(config_file, require_api_key=False)

    assert config.deepseek_api_key is None


def test_missing_api_key_rejected_for_api_use(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        AppConfig.load(config_file, require_api_key=True)


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("concurrency: 0", "concurrency"),
        ("max_retries: -1", "max_retries"),
        ("max_chapters_per_run: 0", "max_chapters_per_run"),
    ],
)
def test_rejects_invalid_limits(tmp_path, monkeypatch, yaml_text, message):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_text, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        AppConfig.load(config_file)


def test_redacts_secret_from_nested_log_values(caplog):
    caplog.set_level(logging.INFO)
    secret = "sk-test-very-secret"
    logging.getLogger("test").info("%s", redact_secrets({"Authorization": f"Bearer {secret}", "error": secret}))

    assert secret not in caplog.text
    assert "***REDACTED***" in caplog.text
