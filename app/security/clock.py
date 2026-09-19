"""UTC helpers for security state stored in SQLite.

Lease/expiry columns are written as naive UTC because SQLite compares DATETIME
columns as strings; aware values are only used at the edges.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """Aware UTC 'now' — the single clock the security layer reads."""

    return dt.datetime.now(dt.UTC)


def to_storage(value: dt.datetime) -> dt.datetime:
    """Naive UTC for a DATETIME column."""

    return value.astimezone(dt.UTC).replace(tzinfo=None)


def from_storage(value: dt.datetime | None) -> dt.datetime | None:
    """Re-attach UTC to a value read back from SQLite."""

    if value is None:
        return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)
