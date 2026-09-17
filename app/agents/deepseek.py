from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any

from app.config import AppConfig, redact_secrets

from .base import (
    EmptyResponseError,
    InvalidResponseError,
    ModelRequest,
    ModelResult,
    ProviderAuthError,
    ProviderBillingError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    TruncatedResponseError,
)


class DeepSeekProvider:
    """Small OpenAI-compatible adapter with bounded transport retries."""

    def __init__(
        self,
        config: AppConfig,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if client is None:
            if config.deepseek_api_key is None:
                raise ProviderAuthError("DEEPSEEK_API_KEY is required for API work")
            from openai import OpenAI

            client = OpenAI(
                api_key=config.deepseek_api_key.get_secret_value(),
                base_url=config.deepseek_base_url,
            )
        self.client = client
        self.sleep = sleep
        self.jitter = jitter
        self.max_retries = max_retries

    def complete_json(self, request: ModelRequest) -> ModelResult:
        combined_prompt = f"{request.system}\n{request.user}"
        if "json" not in combined_prompt.casefold():
            raise ValueError("JSON mode requests must explicitly mention JSON")

        started = time.perf_counter()
        retries = 0
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=request.model,
                    messages=[
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    # DeepSeek V4 enables high-effort thinking by default. These
                    # schema-bound stages need the token budget for the JSON
                    # payload, not hidden reasoning content.
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return self._normalize(response, retries, started)
            except ProviderError:
                raise
            except Exception as exc:
                error = _classify_transport_error(exc, retries=retries)
                if not error.retryable or retries >= self.max_retries:
                    raise error from exc
                delay = (2**retries) * (1 + 0.25 * self.jitter())
                self.sleep(delay)
                retries += 1

    @staticmethod
    def _normalize(response: Any, retries: int, started: float) -> ModelResult:
        choices = getattr(response, "choices", None)
        if not choices:
            raise EmptyResponseError("Provider returned no choices", retries=retries)
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise TruncatedResponseError(
                "Provider response was truncated at the output-token limit",
                retries=retries,
            )
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise EmptyResponseError("Provider returned empty JSON content", retries=retries)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidResponseError(
                f"Provider returned invalid JSON at character {exc.pos}", retries=retries
            ) from None
        if not isinstance(payload, dict):
            raise InvalidResponseError("Provider JSON response must be an object", retries=retries)

        usage = getattr(response, "usage", None)
        return ModelResult(
            payload=payload,
            raw_text=content,
            input_tokens=_nonnegative_int(getattr(usage, "prompt_tokens", 0)),
            output_tokens=_nonnegative_int(getattr(usage, "completion_tokens", 0)),
            elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
            retries=retries,
            model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            finish_reason=finish_reason,
        )


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _classify_transport_error(exc: Exception, *, retries: int) -> ProviderError:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.casefold()
    safe_message = redact_secrets(exc)
    if status in {401, 403} or "authentication" in name or "permission" in name:
        return ProviderAuthError(f"Provider authentication failed: {safe_message}", retries=retries)
    if status == 402:
        return ProviderBillingError(f"Provider billing failed: {safe_message}", retries=retries)
    if status == 429 or "ratelimit" in name or "rate_limit" in name:
        return ProviderRateLimitError(f"Provider rate limit: {safe_message}", retries=retries)
    if (
        isinstance(exc, TimeoutError)
        or "timeout" in name
        or "connection" in name
        or (isinstance(status, int) and status >= 500)
    ):
        return ProviderServerError(f"Provider temporarily unavailable: {safe_message}", retries=retries)
    return ProviderError(f"Provider request failed: {safe_message}", retries=retries)
