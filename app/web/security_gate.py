"""One security pass over every request: session, auth, CSRF, rate limit, headers.

Ordering is deliberate:

1. security headers are prepared first so even rejections and crashes carry them;
2. the client address is resolved before anything is keyed on it;
3. the session is loaded/created before credentials are checked, so a successful Basic
   credential check can rotate the session id (fixation defence);
4. CSRF is validated before the handler runs, for every unsafe method, without exception
   table entries;
5. rate limiting is applied to the expensive/sensitive classes only;
6. unknown exceptions become a stable generic 500 with the same headers.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs

from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool

from app.config import AppConfig
from app.security import csrf as csrf_module
from app.security import headers as headers_module
from app.security.client_ip import ClientAddress, matches_network, resolve_client
from app.security.rate_limit import RateLimitDecision
from app.security.redaction import redact_text
from app.security.sessions import SESSION_COOKIE, Session
from app.security.settings import (
    LOGIN_BUCKET,
    SENSITIVE_BUCKET,
    TASK_BUCKET,
    SecurityRuntime,
)

LOGGER = logging.getLogger("app.web.security")

#: Paths that never require a session, CSRF token or HTTPS upgrade.
PUBLIC_PATHS = frozenset({"/healthz"})

WEAK_LOGIN_MESSAGE = "需要登录"


@dataclass
class RequestSecurity:
    """Per-request scratch state assembled by the middleware."""

    nonce: str
    https: bool
    expected_origin: str
    client: ClientAddress
    session: Session | None = None
    new_session_id: str | None = None
    clear_session: bool = False


class SecurityGate:
    """Callable middleware implementing the policy for one application."""

    def __init__(self, config: AppConfig, runtime: SecurityRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self._sessions_created = 0

    # ------------------------------------------------------------------ entry
    async def __call__(self, request: Request, call_next):
        peer = request.client.host if request.client else None
        state = RequestSecurity(
            nonce=headers_module.new_nonce(),
            https=self._is_https(request, peer),
            expected_origin="",
            client=resolve_client(
                peer=peer,
                forwarded_for=request.headers.get("x-forwarded-for"),
                trusted_proxies=self.runtime.trusted_proxies,
            ),
        )
        state.expected_origin = csrf_module.expected_origin(
            "https" if state.https else "http", request.headers.get("host", "")
        )
        request.state.csp_nonce = state.nonce
        request.state.csrf_token = ""
        request.state.session_authenticated = False

        try:
            response = await self._handle(request, call_next, state)
        except Exception:
            LOGGER.exception(
                "未处理的请求异常 path=%s method=%s", request.url.path, request.method
            )
            response = self._error(500, "internal_error", "服务器内部错误，请稍后重试")
        return self._finalize(request, response, state)

    # ------------------------------------------------------------- main flow
    async def _handle(self, request: Request, call_next, state: RequestSecurity):
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)

        if self.config.web_force_https and not state.https:
            return self._error(
                403,
                "https_required",
                "该部署要求通过 HTTPS 访问；请使用 HTTPS 入口，或检查反向代理是否转发 X-Forwarded-Proto",
            )

        state.session = await run_in_threadpool(
            self.runtime.sessions.load, request.cookies.get(SESSION_COOKIE)
        )
        if state.session is not None:
            request.state.csrf_token = state.session.csrf_token
            request.state.session_authenticated = state.session.authenticated

        credentials_required = bool(self.config.web_username and self.config.web_password)
        safe_method = request.method not in csrf_module.UNSAFE_METHODS
        if state.session is None and not safe_method:
            # An unsafe request without a live session can never present a valid token, so
            # answer immediately instead of minting a session for every probe.
            return self._error(
                403,
                "csrf_no_session",
                "会话不存在或已过期，请重新加载页面后重试",
            )
        if credentials_required:
            rejection = await self._authenticate(request, state)
            if rejection is not None:
                return rejection
        elif state.session is None:
            # Local single-user mode: still issue a session, because CSRF needs one.
            state.session = await run_in_threadpool(
                self._create_session, state.client.address, True, self.config.web_username
            )
            state.new_session_id = state.session.id
            request.state.csrf_token = state.session.csrf_token
            request.state.session_authenticated = True

        if request.method in csrf_module.UNSAFE_METHODS:
            # Size first: a huge body must be refused before anything parses it (the CSRF
            # check has to read JSON bodies to look for the token field).
            oversized = self._check_body_size(request)
            if oversized is not None:
                return oversized
            decision = await self._check_csrf(request, state)
            if not decision.allowed:
                LOGGER.warning(
                    "CSRF 拒绝 path=%s method=%s code=%s",
                    path,
                    request.method,
                    decision.code,
                )
                return self._error(403, decision.code, decision.detail)
            limited = await self._check_rate_limit(request, state)
            if limited is not None:
                return limited

        return await call_next(request)

    # -------------------------------------------------------------- auth & CSRF
    async def _authenticate(self, request: Request, state: RequestSecurity):
        """Verify Basic credentials; returns a rejection response or ``None``."""

        username = self.config.web_username or ""
        password = self.config.web_password
        supplied_username, supplied_password, supplied = self._basic_credentials(request)
        expected_password = password.get_secret_value() if password else ""
        if (
            supplied
            and secrets.compare_digest(supplied_username, username)
            and secrets.compare_digest(supplied_password, expected_password)
        ):
            if state.session is None:
                state.session = await run_in_threadpool(
                    self._create_session, state.client.address, True, username
                )
            elif not state.session.authenticated:
                # Session fixation defence: credentials arriving on an anonymous session
                # get a brand new id (and CSRF token) before anything else happens.
                state.session = await run_in_threadpool(
                    self._rotate_session, state.session, state.client.address, username
                )
            state.new_session_id = state.session.id
            request.state.csrf_token = state.session.csrf_token
            request.state.session_authenticated = True
            await run_in_threadpool(
                self._reset_login_failures, state.client.address, username
            )
            await run_in_threadpool(self._touch_session, state.session)
            return None

        if state.session is not None and state.session.authenticated:
            # An established session cookie is a complete credential.
            await run_in_threadpool(self._touch_session, state.session)
            return None

        # Unauthenticated visitors get an anonymous session (and its CSRF token) so the
        # login form itself is CSRF protected; minting is rate limited per address to keep
        # a credential-less flood from growing the session table.
        if state.session is None and await self._allow_anonymous_session(state):
            state.session = await run_in_threadpool(
                self._create_session, state.client.address, False, None
            )
            state.new_session_id = state.session.id
            request.state.csrf_token = state.session.csrf_token

        if supplied and self.runtime.rate_limit_enabled:
            decision = await run_in_threadpool(
                self._count_login_failure, state.client.address, supplied_username
            )
            if not decision.allowed:
                LOGGER.warning("登录失败次数超限 ip=%s 身份已脱敏", state.client.address)
                return self._error(
                    429,
                    "rate_limited_login",
                    "登录尝试过于频繁，请稍后再试",
                    headers={"Retry-After": str(decision.retry_after)},
                )
        return self._error(
            401,
            "unauthenticated",
            WEAK_LOGIN_MESSAGE,
            headers={
                "WWW-Authenticate": 'Basic realm="IELTS Reading Studio", charset="UTF-8"'
            },
        )

    async def _allow_anonymous_session(self, state: RequestSecurity) -> bool:
        """Rate limit session minting for unauthenticated visitors."""

        if not self.runtime.rate_limit_enabled:
            return True
        policy = self.runtime.policy_for(SENSITIVE_BUCKET)
        decision = await run_in_threadpool(
            self.runtime.limiter.hit, f"{TASK_BUCKET}:anonymous:{state.client.address}", policy
        )
        return decision.allowed

    def _basic_credentials(self, request: Request) -> tuple[str, str, bool]:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return "", "", False
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return "", "", True
        return username, password, True

    def _login_buckets(self, client_address: str, username: str) -> tuple[str, str]:
        """Failures count against the identity *and* the source address.

        Counting both means an attacker cannot dodge the limit by walking user names, and
        the answer never reveals whether the account exists.
        """

        identity = username.strip().casefold() or "-"
        return (
            f"{LOGIN_BUCKET}:identity:{identity}:{client_address}",
            f"{LOGIN_BUCKET}:address:{client_address}",
        )

    def _count_login_failure(self, client_address: str, username: str) -> RateLimitDecision:
        policy = self.runtime.policy_for(LOGIN_BUCKET)
        decisions = [
            self.runtime.limiter.hit(bucket, policy)
            for bucket in self._login_buckets(client_address, username)
        ]
        # The bucket with the most hits decides: `remaining` clamps at zero, so it cannot
        # be used to pick the stricter of the two counters.
        return max(decisions, key=lambda decision: decision.hits)

    def _reset_login_failures(self, client_address: str, username: str) -> None:
        for bucket in self._login_buckets(client_address, username):
            self.runtime.limiter.reset(bucket)

    async def _check_csrf(self, request: Request, state: RequestSecurity):
        submitted = request.headers.get(csrf_module.CSRF_HEADER)
        if not submitted:
            submitted = await self._form_token(request)
        return csrf_module.validate(
            state.session,
            submitted=submitted,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            expected_origin=state.expected_origin,
        )

    async def _form_token(self, request: Request) -> str | None:
        """Find the token in a small body; never touches multipart uploads."""

        content_type = request.headers.get("content-type", "").split(";")[0].strip().casefold()
        if content_type == "application/json":
            # request.json() reads through request.body(), which caches the raw payload so
            # the route can parse it again.
            try:
                payload = await request.json()
            except (ValueError, UnicodeDecodeError):
                return None
            if isinstance(payload, dict):
                value = payload.get(csrf_module.CSRF_FIELD)
                return value if isinstance(value, str) else None
            return None
        if content_type == "application/x-www-form-urlencoded":
            # Parse the cached body ourselves: Starlette's form() would consume the stream
            # and leave the endpoint with an empty form.
            body = await request.body()
            try:
                fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                return None
            values = fields.get(csrf_module.CSRF_FIELD) or []
            return values[0] if values else None
        # Multipart and everything else: the header is required (see module docstring).
        return None

    async def _check_rate_limit(self, request: Request, state: RequestSecurity):
        if not self.runtime.rate_limit_enabled:
            return None
        classification = self.runtime.policy_for_path(request.method, request.url.path)
        if classification is None:
            return None
        bucket_name, policy = classification
        decision = await run_in_threadpool(
            self.runtime.limiter.hit,
            f"{bucket_name}:{state.client.address}",
            policy,
        )
        if decision.allowed:
            return None
        LOGGER.warning(
            "限速拒绝 bucket=%s path=%s ip=%s hits=%s",
            bucket_name,
            request.url.path,
            state.client.address,
            decision.hits,
        )
        return self._error(
            429,
            f"rate_limited_{bucket_name}",
            "请求过于频繁，请稍后再试",
            headers={"Retry-After": str(decision.retry_after)},
        )

    # ----------------------------------------------------------- session plumbing
    def _check_body_size(self, request: Request) -> Response | None:
        """Cap JSON/form bodies before they are parsed.

        Uploads are deliberately exempt: they stream to disk with their own limit, which a
        Content-Length check here would duplicate (and get wrong for chunked uploads).
        """

        content_type = request.headers.get("content-type", "").split(";")[0].strip().casefold()
        if content_type not in {"application/json", "application/x-www-form-urlencoded"}:
            return None
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return self._error(400, "invalid_content_length", "Content-Length 头无效")
        limit = self.config.web_max_json_body_bytes
        if declared > limit:
            return self._error(
                413,
                "body_too_large",
                f"请求体 {declared} 字节，超过上限 {limit} 字节",
            )
        return None

    def _create_session(self, client_address: str, authenticated: bool, username: str | None):
        self._sessions_created += 1
        if self._sessions_created % 100 == 0:
            self.runtime.sessions.purge_expired()
            self.runtime.limiter.purge_expired()
        return self.runtime.sessions.create(
            client_ip=client_address, authenticated=authenticated, username=username
        )

    def _rotate_session(self, session, client_address: str, username: str):
        return self.runtime.sessions.rotate(
            session, authenticated=True, username=username, client_ip=client_address
        )

    def _touch_session(self, session) -> None:
        """Refresh the idle window at most once a minute to keep writes bounded."""

        if session.last_seen_at is None:
            return
        from app.security.clock import utcnow

        if (utcnow() - session.last_seen_at).total_seconds() < 60:
            return
        self.runtime.sessions.touch(session.id)

    # ------------------------------------------------------------- request meta
    def _is_https(self, request: Request, peer: str | None) -> bool:
        if request.url.scheme == "https":
            return True
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded and self.runtime.trusted_proxies and peer and matches_network(
            peer, self.runtime.trusted_proxies
        ):
            return forwarded.split(",")[0].strip().casefold() == "https"
        return False

    # ---------------------------------------------------------------- responses
    def _error(
        self,
        status_code: int,
        code: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Stable, leak-free error body: a code plus a human sentence."""

        import json

        body = json.dumps({"code": code, "detail": redact_text(detail)}, ensure_ascii=False)
        return Response(
            content=body,
            status_code=status_code,
            media_type="application/json; charset=utf-8",
            headers=headers or {},
        )

    def _finalize(self, request: Request, response: Response, state: RequestSecurity) -> Response:
        settings = self.config.session_cookie_settings()
        if state.clear_session:
            response.delete_cookie(SESSION_COOKIE, path=settings["path"])
        elif state.new_session_id:
            response.set_cookie(
                SESSION_COOKIE,
                state.new_session_id,
                max_age=self.config.web_session_absolute_hours * 3600,
                **settings,
            )
        for name, value in headers_module.security_headers(
            nonce=state.nonce, https=state.https
        ).items():
            response.headers.setdefault(name, value)
        if not headers_module.is_cacheable(request.url.path):
            response.headers.setdefault("Cache-Control", headers_module.CACHE_CONTROL_PRIVATE)
            response.headers.setdefault("Pragma", headers_module.PRAGMA_PRIVATE)
        return response



