"""Security response headers and cache policy.

The CSP is written against what the templates actually use: same-origin static JS/CSS,
a handful of inline ``<script>`` blocks (allowed through a per-request nonce), inline
``style`` attributes on a few elements, and no third-party origins at all.
"""

from __future__ import annotations

import secrets

#: Static assets are the only thing worth caching; everything else carries private data.
CACHEABLE_PREFIXES = ("/static/",)
CACHE_CONTROL_PRIVATE = "no-store, no-cache, must-revalidate, max-age=0"
PRAGMA_PRIVATE = "no-cache"


def new_nonce() -> str:
    """Per-response CSP nonce for the inline scripts in the templates."""

    return secrets.token_urlsafe(16)


def is_cacheable(path: str) -> bool:
    return path.startswith(CACHEABLE_PREFIXES)


def content_security_policy(*, nonce: str) -> str:
    """CSP without ``unsafe-eval``/``unsafe-inline`` for scripts.

    ``style-src`` keeps ``unsafe-inline`` because several templates use ``style=""``
    attributes; scripts do not, so XSS via injected ``<script>`` stays blocked.
    """

    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "manifest-src 'self'",
        ]
    )


def security_headers(*, nonce: str, https: bool) -> dict[str, str]:
    """Headers applied to every response, including error responses."""

    headers = {
        "Content-Security-Policy": content_security_policy(nonce=nonce),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        # Kept consistent with `frame-ancestors 'none'` for older browsers.
        "X-Frame-Options": "DENY",
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if https:
        # Only advertised over HTTPS so plain-HTTP local development keeps working.
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
