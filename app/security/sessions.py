"""Server-side browser sessions with rotation, idle and absolute expiry.

Design notes:

* The cookie carries an opaque 256-bit random id; the database stores an HMAC of it.
  A leaked database therefore does not hand out usable session ids, and an attacker who
  can only read cookies still faces server-side invalidation.
* Sessions exist for anonymous visitors too: they hold the CSRF token, which is what
  protects state-changing requests (including ones sent with Basic credentials, which
  browsers attach automatically).
* Signing in again rotates the session id (fixation defence); logging out deletes the
  row, so the cookie value is dead immediately.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from app.security.clock import from_storage, to_storage, utcnow
from app.storage.database import Database, web_sessions

#: Cookie name and scope. Path is narrowed to the site root, no Domain attribute.
SESSION_COOKIE = "ielts_session"
SESSION_PATH = "/"
SESSION_ID_BYTES = 32
CSRF_TOKEN_BYTES = 32


def new_session_id() -> str:
    return secrets.token_hex(SESSION_ID_BYTES)


def new_csrf_token() -> str:
    return secrets.token_hex(CSRF_TOKEN_BYTES)


@dataclass(frozen=True)
class Session:
    """One browser session as the middleware sees it."""

    id: str
    csrf_token: str
    authenticated: bool
    username: str | None
    client_ip: str | None
    created_at: object | None = None
    last_seen_at: object | None = None
    idle_expires_at: object | None = None
    absolute_expires_at: object | None = None


def _idle_delta(minutes: int) -> dt.timedelta:
    return dt.timedelta(minutes=minutes)


def _absolute_delta(hours: int) -> dt.timedelta:
    return dt.timedelta(hours=hours)


class SessionStore:
    """Persistent session table shared by every web process."""

    def __init__(
        self,
        database: Database,
        *,
        secret: str,
        idle_minutes: int,
        absolute_hours: int,
    ) -> None:
        self.database = database
        self._secret = secret.encode("utf-8")
        self.idle_minutes = idle_minutes
        self.absolute_hours = absolute_hours

    # ------------------------------------------------------------------ hashing
    def fingerprint(self, session_id: str) -> str:
        """Keyed hash of the cookie value: what actually lands in the database."""

        return hmac.new(self._secret, session_id.encode("utf-8"), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------- lookup
    def load(self, session_id: str | None) -> Session | None:
        """Return a live session, or ``None`` when the id is missing/expired/unknown."""

        if not session_id or len(session_id) != SESSION_ID_BYTES * 2:
            return None
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(web_sessions).where(
                        web_sessions.c.id_hash == self.fingerprint(session_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        now = to_storage(utcnow())
        idle_expires = row["idle_expires_at"]
        absolute_expires = row["absolute_expires_at"]
        if idle_expires is None or absolute_expires is None:
            self.destroy(session_id)
            return None
        if idle_expires <= now or absolute_expires <= now:
            self.destroy(session_id)
            return None
        return Session(
            id=session_id,
            csrf_token=row["csrf_token"],
            authenticated=bool(row["authenticated"]),
            username=row["username"],
            client_ip=row["client_ip"],
            created_at=from_storage(row["created_at"]),
            last_seen_at=from_storage(row["last_seen_at"]),
            idle_expires_at=from_storage(idle_expires),
            absolute_expires_at=from_storage(absolute_expires),
        )

    # ------------------------------------------------------------------- create
    def create(self, *, client_ip: str | None, authenticated: bool, username: str | None) -> Session:
        """Start a fresh session and return it together with its cookie value."""

        return self._insert(
            session_id=new_session_id(),
            csrf_token=new_csrf_token(),
            client_ip=client_ip,
            authenticated=authenticated,
            username=username,
            absolute_expires_at=to_storage(utcnow()) + _absolute_delta(self.absolute_hours),
        )

    def rotate(
        self,
        session: Session,
        *,
        authenticated: bool,
        username: str | None,
        client_ip: str | None = None,
    ) -> Session:
        """Replace the id (fresh CSRF token) and mark the new session authenticated.

        Called on every successful credential check performed by a session that was not
        authenticated yet, which is what stops session fixation.
        """

        self.destroy(session.id)
        return self._insert(
            session_id=new_session_id(),
            csrf_token=new_csrf_token(),
            client_ip=client_ip if client_ip is not None else session.client_ip,
            authenticated=authenticated,
            username=username,
            absolute_expires_at=to_storage(utcnow()) + _absolute_delta(self.absolute_hours),
        )

    def _insert(
        self,
        *,
        session_id: str,
        csrf_token: str,
        client_ip: str | None,
        authenticated: bool,
        username: str | None,
        absolute_expires_at: dt.datetime,
    ) -> Session:
        now = to_storage(utcnow())
        values = {
            "id_hash": self.fingerprint(session_id),
            "csrf_token": csrf_token,
            "authenticated": 1 if authenticated else 0,
            "username": username,
            "client_ip": client_ip,
            "created_at": now,
            "last_seen_at": now,
            "idle_expires_at": now + _idle_delta(self.idle_minutes),
            "absolute_expires_at": absolute_expires_at,
        }
        with self.database.engine.begin() as connection:
            connection.execute(insert(web_sessions).values(**values))
        stored = self.load(session_id)
        if stored is not None:
            return stored
        return Session(  # pragma: no cover - the row was inserted a moment ago
            id=session_id,
            csrf_token=csrf_token,
            authenticated=authenticated,
            username=username,
            client_ip=client_ip,
        )

    # -------------------------------------------------------------- maintenance
    def touch(self, session_id: str) -> None:
        """Extend the idle window after a request that used the session."""

        now = to_storage(utcnow())
        with self.database.engine.begin() as connection:
            connection.execute(
                update(web_sessions)
                .where(web_sessions.c.id_hash == self.fingerprint(session_id))
                .values(last_seen_at=now, idle_expires_at=now + _idle_delta(self.idle_minutes))
            )

    def destroy(self, session_id: str) -> None:
        """Invalidate a session server-side (logout, rotation, expiry)."""

        with self.database.engine.begin() as connection:
            connection.execute(
                delete(web_sessions).where(web_sessions.c.id_hash == self.fingerprint(session_id))
            )

    def purge_expired(self, *, connection: Connection | None = None) -> int:
        """Drop expired rows; bounded work, called opportunistically."""

        now = to_storage(utcnow())
        statement = delete(web_sessions).where(
            (web_sessions.c.idle_expires_at <= now)
            | (web_sessions.c.absolute_expires_at <= now)
        )
        if connection is not None:
            return connection.execute(statement).rowcount or 0
        with self.database.engine.begin() as owned:
            return owned.execute(statement).rowcount or 0

    def count(self) -> int:
        from sqlalchemy import func

        with self.database.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(web_sessions)).scalar_one())
