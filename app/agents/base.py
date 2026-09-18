from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ModelRequest(BaseModel):
    """One JSON-oriented model invocation."""

    stage: str
    model: str
    system: str
    user: str
    max_tokens: int = Field(gt=0)
    temperature: float = Field(0.2, ge=0, le=2)


class ModelResult(BaseModel):
    """Normalized provider response and auditable usage metadata."""

    payload: dict[str, Any]
    raw_text: str
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    elapsed_ms: int = Field(0, ge=0)
    retries: int = Field(0, ge=0)
    model: str | None = None
    response_id: str | None = None
    finish_reason: str | None = None


class JsonProvider(Protocol):
    """Structural interface shared by real and test JSON providers."""

    def complete_json(self, request: ModelRequest) -> ModelResult: ...


class ProviderError(RuntimeError):
    """Safe, classified provider failure suitable for logs and job state."""

    code = "provider_error"
    retryable = False
    stops_queue = False

    def __init__(self, message: str, *, retries: int = 0):
        super().__init__(message)
        self.retries = retries


class ProviderAuthError(ProviderError):
    code = "authentication_failed"
    stops_queue = True


class ProviderBillingError(ProviderError):
    code = "billing_failed"
    stops_queue = True


class ProviderRateLimitError(ProviderError):
    code = "rate_limited"
    retryable = True


class ProviderServerError(ProviderError):
    code = "provider_unavailable"
    retryable = True


class EmptyResponseError(ProviderError):
    code = "empty_response"
    retryable = True


class TruncatedResponseError(ProviderError):
    code = "truncated_response"
    retryable = True


class InvalidResponseError(ProviderError):
    code = "invalid_json_response"


class AgentSchemaError(RuntimeError):
    """Raised when a syntactically valid provider payload violates an agent contract."""

