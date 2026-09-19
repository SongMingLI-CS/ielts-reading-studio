"""Runtime security policy derived from :class:`AppConfig`.

Keeps ``create_app`` readable: the app asks this module for the session store, the rate
limiter and the policies instead of assembling them inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import AppConfig
from app.security.client_ip import parse_trusted_proxies
from app.security.rate_limit import RateLimiter, RateLimitPolicy
from app.security.sessions import SessionStore
from app.storage.database import Database

#: Route classes used by the middleware to pick a rate-limit policy.
LOGIN_BUCKET = "login"
TASK_BUCKET = "task"
SENSITIVE_BUCKET = "sensitive"

#: Unsafe requests that start provider work or queue paid jobs.
TASK_PATH_SUFFIXES = (
    "/jobs",
    "/sample",
    "/retry",
    "/resume",
    "/generate-sample",
    "/generate-batch",
    "/recover/resume",
    "/recover/retry-failed",
    "/api/writing/evaluate",
    "/settings/test-provider",
)

#: Unsafe requests that touch files, backups, imports or destructive edits.
SENSITIVE_PATH_SUFFIXES = (
    "/import",
    "/rebind",
    "/delete",
    "/boundaries",
    "/boundaries/edit",
    "/exports",
    "/backup/download",
    "/estimate",
    "/logout",
    "/review/sample",
)


@dataclass(frozen=True)
class SecurityRuntime:
    """Everything the middleware needs, built once per application."""

    sessions: SessionStore
    limiter: RateLimiter
    config: AppConfig
    trusted_proxies: tuple = field(default_factory=tuple)
    policies: dict[str, RateLimitPolicy] = field(default_factory=dict)

    @property
    def rate_limit_enabled(self) -> bool:
        return self.config.web_rate_limit_enabled

    def policy_for(self, bucket: str) -> RateLimitPolicy:
        return self.policies[bucket]

    def policy_for_path(self, method: str, path: str) -> tuple[str, RateLimitPolicy] | None:
        """Classify an unsafe request; ``None`` means 'no extra limit beyond CSRF'."""

        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        normalised = path.rstrip("/") or "/"
        if any(normalised.endswith(suffix) for suffix in TASK_PATH_SUFFIXES):
            return TASK_BUCKET, self.policies[TASK_BUCKET]
        if any(normalised.endswith(suffix) for suffix in SENSITIVE_PATH_SUFFIXES):
            return SENSITIVE_BUCKET, self.policies[SENSITIVE_BUCKET]
        return None


def build_security_runtime(config: AppConfig, database: Database) -> SecurityRuntime:
    """Create the session store, limiter and policies for one application."""

    return SecurityRuntime(
        sessions=SessionStore(
            database,
            secret=config.session_secret(),
            idle_minutes=config.web_session_idle_minutes,
            absolute_hours=config.web_session_absolute_hours,
        ),
        limiter=RateLimiter(database),
        config=config,
        trusted_proxies=parse_trusted_proxies(config.web_trusted_proxies),
        policies={
            LOGIN_BUCKET: RateLimitPolicy(
                limit=config.web_login_rate_limit,
                window_seconds=config.web_login_rate_window_seconds,
            ),
            TASK_BUCKET: RateLimitPolicy(
                limit=config.web_task_rate_limit,
                window_seconds=config.web_task_rate_window_seconds,
            ),
            SENSITIVE_BUCKET: RateLimitPolicy(
                limit=config.web_sensitive_rate_limit,
                window_seconds=config.web_sensitive_rate_window_seconds,
            ),
        },
    )
