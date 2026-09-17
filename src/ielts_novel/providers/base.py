from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ielts_novel.models import Chapter, ConvertedChapter, VocabularyItem


class ProviderError(RuntimeError):
    error_type = "provider_error"


class EmptyResponseError(ProviderError):
    error_type = "empty_response"


class InvalidResponseError(ProviderError):
    error_type = "invalid_response"


class ProviderAuthError(ProviderError):
    error_type = "authentication"


class ProviderBillingError(ProviderError):
    error_type = "billing"


class ProviderRateLimitError(ProviderError):
    error_type = "rate_limit"


@dataclass(frozen=True)
class ProviderResult:
    chapter: ConvertedChapter
    raw_content: str
    model: str
    input_tokens: int
    output_tokens: int
    retry_count: int
    elapsed_seconds: float


class ModelProvider(ABC):
    @abstractmethod
    def generate_chapter(self, chapter: Chapter, target_vocabulary: list[VocabularyItem], review_vocabulary: list[VocabularyItem], **kwargs) -> ProviderResult: ...

    @abstractmethod
    def retry_chapter(self, chapter: Chapter, target_vocabulary: list[VocabularyItem], review_vocabulary: list[VocabularyItem], **kwargs) -> ProviderResult: ...

    @abstractmethod
    def complete_json(self, *, system: str, user: str, max_tokens: int = 8192, model: str | None = None) -> tuple[dict, int, int]:
        """Run a structured JSON completion and return (payload, input_tokens, output_tokens)."""

    @abstractmethod
    def health_check(self) -> bool: ...

    @abstractmethod
    def estimate_tokens(self, chapter: Chapter) -> tuple[int, int]: ...

