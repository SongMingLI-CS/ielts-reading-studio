from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator


class ConfigurationError(RuntimeError):
    """Raised when application configuration cannot be loaded safely."""


#: Obvious example/placeholder values that must never protect a public deployment.
_WEAK_PASSWORDS = frozenset(
    {"secret", "password", "changeme", "change-me", "reader", "ielts", "123456", "admin", "test"}
)
_WEAK_SECRETS = frozenset(
    {
        "development-only-session-key",
        "change-me",
        "changeme",
        "example-session-secret",
        "0" * 32,
    }
)


def redact_secrets(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)(Bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(DEEPSEEK_API_KEY\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(IELTS_WEB_PASSWORD\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(IELTS_WEB_SESSION_SECRET\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text


class AppConfig(BaseModel):
    base_dir: Path
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    database_path: Path = Path("output/state.db")
    deepseek_api_key: SecretStr | None = None
    web_username: str | None = None
    web_password: SecretStr | None = None
    # Minimum length enforced for a publicly reachable deployment. Raising it is always
    # safe; lowering it is a deliberate, documented risk decision (docs/security.md 第 4.8
    # 节与第 5 节) - the compensating control is the login rate limit plus the single
    # entry point. The floor stops a typo from disabling the check entirely.
    web_password_min_length: int = Field(12, ge=8, le=128)
    deepseek_base_url: str = "https://api.deepseek.com"
    author_model: str = "deepseek-flash"
    examiner_model: str = "deepseek-v4-pro"
    writing_model: str = "deepseek-v4-pro"
    writing_max_output_tokens: int = Field(4000, ge=500, le=16_000)
    concurrency: int = Field(2, ge=1, le=8)
    batch_size: int = Field(20, ge=1, le=100)
    author_revision_limit: int = Field(2, ge=0, le=2)
    examiner_revision_limit: int = Field(2, ge=0, le=2)
    min_source_chars: int = Field(800, ge=100)
    max_merged_chapters: int = Field(4, ge=1, le=20)
    max_merged_chars: int = Field(4000, ge=500)
    split_source_chars: int = Field(6000, ge=1000)
    split_min_chars: int = Field(2500, ge=500)
    split_max_chars: int = Field(4500, ge=1000)
    max_units_per_run: int = Field(20, ge=1)
    max_estimated_tokens_per_run: int = Field(500_000, ge=1)
    max_consecutive_failures: int = Field(5, ge=1)
    # 题目相似度去重：达到阈值即判为重复题，进入返工；审阅页用更宽松的阈值列出疑似。
    question_duplicate_threshold: float = Field(0.72, ge=0.3, le=1.0)
    question_report_threshold: float = Field(0.5, ge=0.2, le=1.0)
    # 抽样审阅：一批完成后自动抽取的比例与下限。
    review_sample_rate: float = Field(0.1, ge=0.0, le=1.0)
    review_sample_min: int = Field(1, ge=0, le=50)
    # 持久任务队列：worker 认领作业后持有租约，崩溃后由其它 worker 安全接管。
    job_lease_seconds: int = Field(300, ge=30, le=3600)
    max_active_jobs: int = Field(2, ge=1, le=8)

    # ---------------------------------------------------------------- 安全与会话
    # 会话密钥只从环境变量读取，用于对会话 ID 做 HMAC，生产环境必须显式提供。
    web_session_secret: SecretStr | None = None
    web_session_idle_minutes: int = Field(120, ge=5, le=10080)
    web_session_absolute_hours: int = Field(24, ge=1, le=720)
    # True 时 Cookie 带 Secure；None 表示按部署方式自动判断。
    web_cookie_secure: bool | None = None
    # 声明部署在 HTTPS 之后（反向代理终止 TLS），公网部署必须为 True。
    web_force_https: bool = False
    # 逗号分隔的可信代理 IP/CIDR；为空表示不信任任何转发头。
    web_trusted_proxies: str = ""
    web_rate_limit_enabled: bool = True
    web_login_rate_limit: int = Field(10, ge=1, le=1000)
    web_login_rate_window_seconds: int = Field(300, ge=10, le=86400)
    web_task_rate_limit: int = Field(10, ge=1, le=1000)
    web_task_rate_window_seconds: int = Field(60, ge=5, le=86400)
    web_sensitive_rate_limit: int = Field(30, ge=1, le=1000)
    web_sensitive_rate_window_seconds: int = Field(60, ge=5, le=86400)
    # JSON 请求体积上限（表单与上传各自有更细的限制）。
    web_max_json_body_bytes: int = Field(1_000_000, ge=1024, le=64_000_000)
    web_max_upload_bytes: int = Field(250 * 1024 * 1024, ge=1_048_576, le=2_000_000_000)
    # ------------------------------------------------------------ Provider 预算
    max_prompt_chars: int = Field(60_000, ge=1_000, le=1_000_000)
    max_output_tokens: int = Field(16_000, ge=500, le=64_000)
    provider_timeout_seconds: float = Field(60.0, ge=5.0, le=600.0)
    provider_connect_timeout_seconds: float = Field(10.0, ge=1.0, le=120.0)
    provider_max_retries: int = Field(3, ge=0, le=5)
    max_writing_chars: int = Field(12_000, ge=500, le=100_000)

    @model_validator(mode="before")
    @classmethod
    def anchor_relative_paths_on_base_dir(cls, data: Any) -> Any:
        """Keep relative data paths inside base_dir instead of the process CWD.

        AppConfig.load() already resolves input/output/database against the YAML
        file, so configs read from disk arrive here absolute and untouched. A
        programmatically built config (tests, scripts, a library user) keeps the
        bare "output/..." defaults, and those used to resolve against whatever
        directory the process happened to run in: a test run could therefore
        write corpora and units into a real data directory. base_dir is required
        for YAML-free construction, so it is always a safe anchor.
        """
        if not isinstance(data, dict):
            return data
        base = data.get("base_dir")
        if base is None:
            return data
        base_path = Path(base)
        anchored = dict(data)
        for field in ("input_dir", "output_dir", "database_path"):
            candidate = Path(anchored.get(field, cls.model_fields[field].default))
            if not candidate.is_absolute():
                anchored[field] = base_path / candidate
        return anchored

    @model_validator(mode="after")
    def validate_web_credentials(self) -> AppConfig:
        if bool(self.web_username) != bool(self.web_password):
            raise ValueError("IELTS_WEB_USERNAME and IELTS_WEB_PASSWORD must be configured together")
        return self

    # ------------------------------------------------------------------ 安全派生
    def session_secret(self) -> str:
        """Key used to hash session ids; development falls back to a fixed local value.

        The fallback is deliberately *not* random: a random per-process key would silently
        invalidate every session on restart, and production refuses to start without a
        real secret (see :meth:`production_issues`).
        """

        if self.web_session_secret is not None:
            return self.web_session_secret.get_secret_value()
        return "development-only-session-key"

    def cookies_secure(self) -> bool:
        """Whether cookies carry the ``Secure`` attribute."""

        if self.web_cookie_secure is not None:
            return self.web_cookie_secure
        return self.web_force_https

    def session_cookie_settings(self) -> dict[str, object]:
        """Cookie attributes shared by the login and logout handlers."""

        return {
            "httponly": True,
            "samesite": "lax",
            "secure": self.cookies_secure(),
            "path": "/",
        }

    def production_issues(self, *, host: str) -> list[str]:
        """Reasons this configuration must not be exposed publicly (empty list = fine).

        Only called when the process binds a non-loopback address, so local development
        and the test suite keep their current behaviour.
        """

        issues: list[str] = []
        if not self.web_username or not self.web_password:
            issues.append("远程监听必须先设置 IELTS_WEB_USERNAME 与 IELTS_WEB_PASSWORD")
        else:
            password = self.web_password.get_secret_value()
            if len(password) < self.web_password_min_length:
                issues.append(
                    f"IELTS_WEB_PASSWORD 至少需要 {self.web_password_min_length} 个字符"
                    "（可用 IELTS_WEB_PASSWORD_MIN_LENGTH 显式放宽，最低 8）"
                )
            if password.casefold() in _WEAK_PASSWORDS:
                issues.append("IELTS_WEB_PASSWORD 使用了示例或常见弱口令，请更换")
        if not self.web_force_https:
            issues.append(
                "公网部署必须声明 HTTPS：设置 IELTS_WEB_FORCE_HTTPS=1（并在反向代理上终止 TLS）"
            )
        secret = self.web_session_secret.get_secret_value() if self.web_session_secret else ""
        if not secret:
            issues.append("生产环境必须设置 IELTS_WEB_SESSION_SECRET（至少 32 个字符）")
        elif secret.casefold() in _WEAK_SECRETS:
            issues.append("IELTS_WEB_SESSION_SECRET 使用了示例值，请更换")
        elif len(secret) < 32:
            issues.append("IELTS_WEB_SESSION_SECRET 至少需要 32 个字符")
        if self.web_trusted_proxies.strip() == "" and self.web_force_https:
            issues.append(
                "声明使用 HTTPS 时必须设置 IELTS_WEB_TRUSTED_PROXIES，"
                "否则无法从 X-Forwarded-Proto 判断真实协议"
            )
        return issues

    @classmethod
    def load(cls, path: str | Path, require_api_key: bool = False) -> AppConfig:
        config_path = Path(path).expanduser().resolve()
        config_parent = config_path.parent
        load_dotenv(config_parent / ".env", override=False)
        raw: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise ConfigurationError(redact_secrets(exc)) from exc
        elif require_api_key and not os.getenv("DEEPSEEK_API_KEY"):
            raise ConfigurationError("DEEPSEEK_API_KEY is required for API work")
        elif not config_path.exists():
            raise ConfigurationError(f"Configuration file not found: {config_path}")
        if not isinstance(raw, dict):
            raise ConfigurationError("Configuration YAML must contain a mapping")
        explicit_base = raw.get("base_dir")
        base_dir = Path(explicit_base) if explicit_base else config_parent
        if not base_dir.is_absolute():
            base_dir = config_parent / base_dir
        raw["base_dir"] = base_dir.resolve()
        # These paths are relative to the YAML file, while base_dir itself is
        # independently configurable and may point elsewhere.
        for field in ("input_dir", "output_dir", "database_path"):
            candidate = Path(raw.get(field, cls.model_fields[field].default))
            raw[field] = candidate if candidate.is_absolute() else config_parent / candidate
        # Secrets are accepted only from the process environment/.env.
        for secret_field in ("deepseek_api_key", "web_username", "web_password", "web_session_secret"):
            raw.pop(secret_field, None)
        if os.getenv("DEEPSEEK_API_KEY"):
            raw["deepseek_api_key"] = os.environ["DEEPSEEK_API_KEY"]
        if os.getenv("IELTS_WEB_USERNAME"):
            raw["web_username"] = os.environ["IELTS_WEB_USERNAME"]
        if os.getenv("IELTS_WEB_PASSWORD"):
            raw["web_password"] = os.environ["IELTS_WEB_PASSWORD"]
        if os.getenv("IELTS_WEB_PASSWORD_MIN_LENGTH"):
            raw["web_password_min_length"] = os.environ["IELTS_WEB_PASSWORD_MIN_LENGTH"].strip()
        if os.getenv("IELTS_WEB_SESSION_SECRET"):
            raw["web_session_secret"] = os.environ["IELTS_WEB_SESSION_SECRET"]
        if os.getenv("IELTS_WEB_FORCE_HTTPS"):
            raw["web_force_https"] = os.environ["IELTS_WEB_FORCE_HTTPS"].strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if os.getenv("IELTS_WEB_TRUSTED_PROXIES"):
            raw["web_trusted_proxies"] = os.environ["IELTS_WEB_TRUSTED_PROXIES"]
        if os.getenv("IELTS_WEB_COOKIE_SECURE"):
            raw["web_cookie_secure"] = os.environ["IELTS_WEB_COOKIE_SECURE"].strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if require_api_key and not raw.get("deepseek_api_key"):
            raise ConfigurationError("DEEPSEEK_API_KEY is required for API work")
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigurationError(redact_secrets(exc)) from None
