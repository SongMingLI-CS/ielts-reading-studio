import pytest

from app.config import AppConfig, ConfigurationError, redact_secrets


def test_loads_yaml_without_api_key_for_offline_work(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("output_dir: generated\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.base_dir == tmp_path
    assert config.output_dir == tmp_path / "generated"
    assert config.author_model == "deepseek-flash"


def test_requires_key_only_for_api_work(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        AppConfig.load(tmp_path / "missing.yaml", require_api_key=True)


def test_redacts_keys_and_bearer_tokens():
    assert "secret-value" not in redact_secrets("Bearer secret-value")
    assert "sk-1234567890abcdef" not in redact_secrets("sk-1234567890abcdef")


def test_explicit_base_dir_and_env_are_resolved_from_yaml_parent(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "config.yaml"
    path.parent.mkdir()
    (path.parent / ".env").write_text("DEEPSEEK_API_KEY=env-secret\n", encoding="utf-8")
    path.write_text("base_dir: workspace\ninput_dir: source\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = AppConfig.load(path, require_api_key=True)
    assert config.base_dir == path.parent / "workspace"
    assert config.input_dir == path.parent / "source"
    assert config.deepseek_api_key.get_secret_value() == "env-secret"


def test_yaml_cannot_supply_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("deepseek_api_key: yaml-secret\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.deepseek_api_key is None
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        AppConfig.load(path, require_api_key=True)


def test_web_credentials_are_loaded_only_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "web_username: yaml-user\nweb_password: yaml-password\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IELTS_WEB_USERNAME", "env-user")
    monkeypatch.setenv("IELTS_WEB_PASSWORD", "env-password")

    config = AppConfig.load(path)

    assert config.web_username == "env-user"
    assert config.web_password.get_secret_value() == "env-password"


def test_web_credentials_must_be_configured_together(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("IELTS_WEB_USERNAME", "env-user")
    monkeypatch.delenv("IELTS_WEB_PASSWORD", raising=False)

    with pytest.raises(ConfigurationError, match="must be configured together"):
        AppConfig.load(path)


def test_revision_limits_cannot_exceed_two(tmp_path):
    for field in ("author_revision_limit", "examiner_revision_limit"):
        path = tmp_path / f"{field}.yaml"
        path.write_text(f"{field}: 3\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match=field):
            AppConfig.load(path)
