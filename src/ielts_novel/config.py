from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator


class ConfigurationError(ValueError):
    """Raised when configuration is unsafe or invalid."""


class DensityConfig(BaseModel):
    min_per_500_chars: int = Field(20, ge=1)
    target_per_500_chars: int = Field(28, ge=1)
    max_per_500_chars: int = Field(35, ge=1)

    @model_validator(mode="after")
    def ordered(self) -> "DensityConfig":
        if not self.min_per_500_chars <= self.target_per_500_chars <= self.max_per_500_chars:
            raise ValueError("density must satisfy min <= target <= max")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    deepseek_api_key: SecretStr | None = Field(default=None, repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_review_model: str = "deepseek-v4-pro"
    concurrency: int = Field(3, ge=1, le=16)
    max_retries: int = Field(3, ge=0, le=10)
    max_chapters_per_run: int = Field(20, ge=1)
    max_estimated_tokens_per_run: int = Field(500_000, ge=1)
    max_consecutive_failures: int = Field(5, ge=1, le=50)
    volume_size: int = Field(50, ge=1)
    check_report_every: int = Field(20, ge=1)
    chapter_chunk_chars: int = Field(12_000, ge=500)
    vocabulary_path: Path = Path("data/vocabulary.json")
    batch_confirmed: bool = False
    density: DensityConfig = Field(default_factory=DensityConfig)

    @classmethod
    def load(cls, path: str | Path = "config.yaml", *, require_api_key: bool = False) -> "AppConfig":
        config_path = Path(path).resolve()
        load_dotenv(config_path.parent / ".env", override=False)
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            raw = raw or {}
            if not isinstance(raw, dict):
                raise ConfigurationError("config root must be a mapping")
            env_map = {
                "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY"),
                "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL"),
                "deepseek_model": os.getenv("DEEPSEEK_MODEL"),
                "deepseek_review_model": os.getenv("DEEPSEEK_REVIEW_MODEL"),
            }
            raw.update({key: value for key, value in env_map.items() if value})
            config = cls.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
            raise ConfigurationError(redact_secrets(str(exc))) from exc
        if require_api_key and config.deepseek_api_key is None:
            raise ConfigurationError("DEEPSEEK_API_KEY is required for API operations")
        return config


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(Bearer\s+)[^\s,}\]]+"),
)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if str(key).lower() in {"authorization", "api_key", "apikey"} else redact_secrets(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_secrets(item) for item in value)
    if not isinstance(value, str):
        return value
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: f"{match.group(1)}***REDACTED***" if match.lastindex else "***REDACTED***", result)
    return result

