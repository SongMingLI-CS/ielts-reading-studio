"""Persistent fixed-window rate limiting on the existing SQLite database.

Why SQLite and not an in-process dict: the app runs a web process and a worker process,
may be restarted by a deploy, and must still count login failures. One row per
``(bucket, window_start)`` with a single upsert makes the increment atomic, so
concurrent requests cannot slip past the limit.

Rows are purged opportunistically (``purge_expired``), which keeps the table small
without a background job.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.security.clock import from_storage, to_storage, utcnow
from app.storage.database import Database, rate_limit_hits


@dataclass(frozen=True)
class RateLimitPolicy:
    """``limit`` hits are allowed per ``window_seconds`` window."""

    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("rate limit must allow at least one hit")
        if self.window_seconds < 1:
            raise ValueError("rate limit window must be at least one second")


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of one hit; ``retry_after`` is what the caller puts in the header."""

    allowed: bool
    bucket: str
    hits: int
    limit: int
    retry_after: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.hits)


class RateLimiter:
    """Fixed-window counter store shared by all web processes."""

    def __init__(self, database: Database, *, clock: Callable[[], dt.datetime] = utcnow) -> None:
        self.database = database
        self._clock = clock

    def hit(self, bucket: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """Record one attempt and decide whether it is allowed."""

        now = self._clock().astimezone(dt.UTC)
        window_start = now - dt.timedelta(seconds=now.timestamp() % policy.window_seconds)
        window_end = window_start + dt.timedelta(seconds=policy.window_seconds)
        statement = (
            sqlite_insert(rate_limit_hits)
            .values(
                bucket=bucket,
                window_started_at=to_storage(window_start),
                hits=1,
                expires_at=to_storage(window_end),
            )
            .on_conflict_do_update(
                index_elements=["bucket", "window_started_at"],
                set_={"hits": rate_limit_hits.c.hits + 1},
            )
            .returning(rate_limit_hits.c.hits)
        )
        with self.database.engine.begin() as connection:
            hits = int(connection.execute(statement).scalar_one())
        retry_after = max(1, int((window_end - now).total_seconds()))
        return RateLimitDecision(
            allowed=hits <= policy.limit,
            bucket=bucket,
            hits=hits,
            limit=policy.limit,
            retry_after=retry_after,
        )

    def peek(self, bucket: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """Read the current window without counting a hit (used by tests and UI)."""

        now = self._clock().astimezone(dt.UTC)
        window_start = to_storage(
            now - dt.timedelta(seconds=now.timestamp() % policy.window_seconds)
        )
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(rate_limit_hits.c.hits, rate_limit_hits.c.expires_at).where(
                    rate_limit_hits.c.bucket == bucket,
                    rate_limit_hits.c.window_started_at == window_start,
                )
            ).one_or_none()
        hits = int(row[0]) if row is not None else 0
        expires = from_storage(row[1]) if row is not None else None
        retry_after = max(1, int((expires - now).total_seconds())) if expires else policy.window_seconds
        return RateLimitDecision(
            allowed=hits <= policy.limit,
            bucket=bucket,
            hits=hits,
            limit=policy.limit,
            retry_after=retry_after,
        )

    def reset(self, bucket: str) -> int:
        """Clear a bucket (for example after a successful login)."""

        with self.database.engine.begin() as connection:
            result = connection.execute(
                delete(rate_limit_hits).where(rate_limit_hits.c.bucket == bucket)
            )
        return result.rowcount or 0

    def purge_expired(self) -> int:
        """Drop windows that can no longer affect any decision."""

        now = to_storage(self._clock())
        with self.database.engine.begin() as connection:
            result = connection.execute(
                delete(rate_limit_hits).where(rate_limit_hits.c.expires_at <= now)
            )
        return result.rowcount or 0

    def row_count(self) -> int:
        with self.database.engine.connect() as connection:
            return int(
                connection.execute(select(func.count()).select_from(rate_limit_hits)).scalar_one()
            )
