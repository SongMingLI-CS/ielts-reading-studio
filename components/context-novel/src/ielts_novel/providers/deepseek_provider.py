from __future__ import annotations

import json
import logging
import random
import time
from typing import Callable

from openai import OpenAI
from pydantic import ValidationError

from ielts_novel.config import AppConfig, redact_secrets
from ielts_novel.models import Chapter, ConvertedChapter, VocabularyItem
from ielts_novel.prompts import SYSTEM_PROMPT, build_user_prompt
from ielts_novel.providers.base import EmptyResponseError, InvalidResponseError, ModelProvider, ProviderAuthError, ProviderBillingError, ProviderError, ProviderRateLimitError, ProviderResult


LOGGER = logging.getLogger(__name__)


class DeepSeekProvider(ModelProvider):
    def __init__(self, config: AppConfig, *, client=None, sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random):
        if config.deepseek_api_key is None:
            raise ProviderAuthError("DEEPSEEK_API_KEY is missing")
        self.config = config
        self.client = client or OpenAI(api_key=config.deepseek_api_key.get_secret_value(), base_url=config.deepseek_base_url, timeout=120.0, max_retries=0)
        self.sleeper = sleeper
        self.jitter = jitter

    def estimate_tokens(self, chapter: Chapter) -> tuple[int, int]:
        chars = sum(len(p.text) for p in chapter.paragraphs)
        return max(1, int(chars / 1.5) + 1200), max(2048, int(chars * 1.8))

    def generate_chapter(self, chapter: Chapter, target_vocabulary: list[VocabularyItem], review_vocabulary: list[VocabularyItem], **kwargs) -> ProviderResult:
        return self._generate(chapter, target_vocabulary, review_vocabulary, model=kwargs.get("model", self.config.deepseek_model), retry_note=kwargs.get("retry_note"))

    def retry_chapter(self, chapter: Chapter, target_vocabulary: list[VocabularyItem], review_vocabulary: list[VocabularyItem], **kwargs) -> ProviderResult:
        return self._generate(chapter, target_vocabulary, review_vocabulary, model=kwargs.get("model", self.config.deepseek_review_model), retry_note=kwargs.get("retry_note", "质量检查未通过"))

    @staticmethod
    def _is_retryable(exc: Exception, status: int | None) -> bool:
        """Transport failures must be retried.

        The OpenAI SDK's connection and timeout errors are not ``TimeoutError`` subclasses, so the
        class name is checked too; otherwise a single transient network hiccup would fail a chapter
        in the middle of a 2,000 chapter run.
        """
        if isinstance(exc, (TimeoutError, ConnectionError, OSError, EmptyResponseError, InvalidResponseError)):
            return True
        if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
            return True
        name = type(exc).__name__.lower()
        return any(marker in name for marker in ("timeout", "connection", "protocol", "transport", "network", "reset"))

    def _chat(self, *, system: str, user: str, model: str, max_tokens: int):
        """Single retrying chat call with DeepSeek error classification."""
        started = time.monotonic()
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=min(384_000, max(1024, max_tokens)),
                    extra_body={"thinking": {"type": "disabled"}},
                )
                if not response or not getattr(response, "choices", None):
                    raise EmptyResponseError("empty_response")
                choice = response.choices[0]
                content = getattr(getattr(choice, "message", None), "content", None)
                if not content or not str(content).strip():
                    raise EmptyResponseError("empty_response")
                if getattr(choice, "finish_reason", None) == "length":
                    raise InvalidResponseError("response_truncated")
                usage = getattr(response, "usage", None)
                return str(content), int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0), attempt, time.monotonic() - started
            except (ProviderAuthError, ProviderBillingError):
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status == 401:
                    raise ProviderAuthError("DeepSeek authentication failed") from None
                if status == 402:
                    raise ProviderBillingError("DeepSeek balance is insufficient") from None
                retryable = self._is_retryable(exc, status)
                if not retryable:
                    raise ProviderError(redact_secrets(str(exc))) from None
                if attempt >= self.config.max_retries:
                    if status == 429:
                        raise ProviderRateLimitError("DeepSeek rate limit persisted after retries") from None
                    raise exc
                delay = min(32, 2 ** (attempt + 1)) + self.jitter()
                LOGGER.warning("DeepSeek request retry %s after %s", attempt + 1, redact_secrets(type(exc).__name__))
                self.sleeper(delay)
        raise ProviderError("DeepSeek request failed without a response")

    def complete_json(self, *, system: str, user: str, max_tokens: int = 8192, model: str | None = None) -> tuple[dict, int, int]:
        content, input_tokens, output_tokens, _retry, _elapsed = self._chat(system=system, user=user, model=model or self.config.deepseek_model, max_tokens=max_tokens)
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidResponseError("invalid_json") from exc
        if not isinstance(payload, dict):
            raise InvalidResponseError("json_response_must_be_an_object")
        return payload, input_tokens, output_tokens

    def _generate(self, chapter, target, review, *, model: str, retry_note: str | None) -> ProviderResult:
        _input, output_estimate = self.estimate_tokens(chapter)
        content, input_tokens, output_tokens, retry_count, elapsed = self._chat(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(chapter, target, review, self.config.density.model_dump(), retry_note=retry_note),
            model=model,
            max_tokens=max(6000, output_estimate),
        )
        try:
            chapter_result = ConvertedChapter.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise InvalidResponseError("invalid_json") from exc
        return ProviderResult(chapter_result, content, model, input_tokens, output_tokens, retry_count, elapsed)

    def health_check(self) -> bool:
        try:
            response = self.client.models.list()
            return response is not None
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 401:
                raise ProviderAuthError("DeepSeek authentication failed") from None
            if status == 402:
                raise ProviderBillingError("DeepSeek balance is insufficient") from None
            raise ProviderError(redact_secrets(str(exc))) from None
