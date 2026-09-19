"""CSRF protection bound to the server-side session.

Rules:

* Unsafe methods (POST/PUT/PATCH/DELETE) must present the session's CSRF token, either in
  the ``X-CSRF-Token`` header or as a ``csrf_token`` form field.
* Multipart uploads are header-only on purpose: reading a multipart body inside the
  middleware would buffer the whole upload before the route's streaming size cap runs.
  ``static/security.js`` submits those forms with fetch and the header.
* ``Origin``/``Referer`` are checked as defence in depth when the browser sends them, but
  they are never the only control.
* Tokens are compared with :func:`secrets.compare_digest` and are never logged.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.security.sessions import Session

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"

#: Body types the middleware is willing to parse while hunting for the field. Everything
#: else (multipart in particular) must use the header.
FIELD_PARSEABLE_TYPES = (
    "application/x-www-form-urlencoded",
    "application/json",
    "text/plain",
)


@dataclass(frozen=True)
class CsrfDecision:
    """Outcome of one CSRF check; ``code`` is stable and safe to assert on."""

    allowed: bool
    code: str
    detail: str


def validate(
    session: Session | None,
    *,
    submitted: str | None,
    origin: str | None,
    referer: str | None,
    expected_origin: str,
) -> CsrfDecision:
    """Check the token and, when present, the request origin."""

    if session is None:
        return CsrfDecision(False, "csrf_no_session", "会话不存在或已过期，请重新加载页面")
    if not submitted:
        return CsrfDecision(False, "csrf_missing_token", "缺少 CSRF 令牌，请刷新页面后重试")
    if not secrets.compare_digest(submitted, session.csrf_token):
        return CsrfDecision(False, "csrf_invalid_token", "CSRF 令牌无效，请刷新页面后重试")
    if not same_origin(origin=origin, referer=referer, expected_origin=expected_origin):
        return CsrfDecision(False, "csrf_bad_origin", "请求来源与本站不一致，已拒绝")
    return CsrfDecision(True, "ok", "")


def same_origin(*, origin: str | None, referer: str | None, expected_origin: str) -> bool:
    """True when the browser-provided origin matches this deployment.

    Clients that are not browsers (curl, scripts) send neither header; they still have to
    present the session token, so a missing origin is not treated as an attack.
    """

    if origin:
        return _origin_of(origin) == expected_origin
    if referer:
        return _origin_of(referer) == expected_origin
    return True


def _origin_of(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".casefold()


def expected_origin(scheme: str, host: str) -> str:
    """Build the origin string a same-site request must carry."""

    return f"{scheme}://{host}".casefold()
