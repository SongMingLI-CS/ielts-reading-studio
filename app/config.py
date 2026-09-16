from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError


class ConfigurationError(RuntimeError):
    """Raised when application configuration cannot be loaded safely."""


def redact_secrets(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)(Bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(DEEPSEEK_API_KEY\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text


class AppConfig(BaseModel):
    base_dir: Path
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    database_path: Path = Path("output/state.db")
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    author_model: str = "deepseek-flash"
    examiner_model: str = "deepseek-v4-pro"
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
        raw.pop("deepseek_api_key", None)
        if os.getenv("DEEPSEEK_API_KEY"):
            raw["deepseek_api_key"] = os.environ["DEEPSEEK_API_KEY"]
        if require_api_key and not raw.get("deepseek_api_key"):
            raise ConfigurationError("DEEPSEEK_API_KEY is required for API work")
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigurationError(redact_secrets(exc)) from None
