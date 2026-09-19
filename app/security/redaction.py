"""Scrubbing sensitive values out of logs and error surfaces.

Built on top of :func:`app.config.redact_secrets` so there is one vocabulary for secret
shapes. Only *prefixed* forms are matched (``ielts_session=<value>``, ``X-CSRF-Token:
<value>``, ``Authorization: Basic <value>``), which keeps unrelated hashes such as corpus
checksums readable in diagnostics.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.config import redact_secrets

REDACTED = "[REDACTED]"

#: Header names whose values must never be printed.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "x-api-key",
        "api-key",
    }
)

_SENSITIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)(authorization\s*[:=]\s*)(basic|bearer)\s+[^\s,;]+", r"\1\2 [REDACTED]"),
    (r"(?i)(proxy-authorization\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
    (r"(?i)(ielts_session\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
    (r"(?i)(csrf[_-]?token\"?\s*[:=]\s*\"?)[A-Fa-f0-9]{16,}", r"\1[REDACTED]"),
    (r"(?i)(cookie\s*[:=]\s*)[^\n]+", r"\1[REDACTED]"),
    (r"(?i)(set-cookie\s*[:=]\s*)[^\n]+", r"\1[REDACTED]"),
    (r"(?i)(password\"?\s*[:=]\s*\"?)[^\s,;\"}]+", r"\1[REDACTED]"),
    (r"(?i)\b(sqlite|postgres(?:ql)?|mysql|redis)://[^\s/@:]+:[^\s/@]+@", r"\1://[REDACTED]@"),
)


def redact_text(value: Any) -> str:
    """Full-strength scrubbing for log lines and user-visible errors."""

    import re

    text = redact_secrets(value)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def redact_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
    """Copy headers with sensitive values replaced, safe for logging."""

    items = headers.items() if isinstance(headers, Mapping) else headers
    return {
        str(name): (REDACTED if str(name).casefold() in SENSITIVE_HEADERS else str(value))
        for name, value in items
    }


def summarize(value: str, *, limit: int = 120) -> str:
    """Describe a payload without quoting it: length plus a short digest."""

    import hashlib

    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"len={len(value)} sha256={digest} preview={value[:limit]!r}"
