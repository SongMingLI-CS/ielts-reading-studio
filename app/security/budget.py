"""Server-side budgets for anything that can cost money or minutes.

Every limit here is enforced in Python, never only in the browser widgets:
request size, prompt size, output tokens, retries, timeouts and the estimated cost of a
batch job before it is queued.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of one budget check; ``code`` is stable for tests and clients."""

    allowed: bool
    code: str
    detail: str = ""
    value: int | None = None


class BudgetError(RuntimeError):
    """Raised when a request would exceed a configured budget.

    Carries a stable ``code`` so the web layer can answer 413/429 instead of leaking an
    internal error, and so the queue can record a structured failure.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code

    @property
    def status_code(self) -> int:
        return 413 if self.code in {"input_too_large", "prompt_too_large"} else 429


def enforce(decision: BudgetDecision) -> None:
    """Raise :class:`BudgetError` when a decision is a refusal."""

    if not decision.allowed:
        raise BudgetError(decision.code, decision.detail)


def check_text_length(text: str, *, limit: int, label: str) -> BudgetDecision:
    """Reject oversized user input before it reaches a prompt or the queue."""

    if limit < 1:
        raise ValueError("text limit must be positive")
    length = len(text)
    if length > limit:
        return BudgetDecision(
            allowed=False,
            code="input_too_large",
            detail=f"{label} 长度为 {length} 字符，超过上限 {limit}",
            value=length,
        )
    return BudgetDecision(allowed=True, code="ok", value=length)


def check_prompt(system: str, user: str, *, max_chars: int) -> BudgetDecision:
    """Cap the combined prompt size handed to a provider."""

    total = len(system) + len(user)
    if total > max_chars:
        return BudgetDecision(
            allowed=False,
            code="prompt_too_large",
            detail=f"提示词合计 {total} 字符，超过上限 {max_chars}",
            value=total,
        )
    return BudgetDecision(allowed=True, code="ok", value=total)


def clamp_max_tokens(requested: int, *, cap: int, floor: int = 1) -> int:
    """Clamp a caller-supplied output-token budget into the allowed range."""

    if cap < floor:
        raise ValueError("token cap must not be below the floor")
    return max(floor, min(int(requested), cap))


def check_batch_cost(*, estimated_tokens: int, limit: int, units: int) -> BudgetDecision:
    """Refuse to queue a batch whose offline estimate already exceeds the budget."""

    if estimated_tokens > limit:
        return BudgetDecision(
            allowed=False,
            code="budget_exceeded",
            detail=(
                f"该批次预计 {estimated_tokens} Token（{units} 个单元），"
                f"超过单次上限 {limit}；请缩小范围或提高上限后再提交"
            ),
            value=estimated_tokens,
        )
    return BudgetDecision(allowed=True, code="ok", value=estimated_tokens)
