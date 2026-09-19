"""Versioned schema migrations (Alembic) for the SQLite state database.

Why this module exists instead of calling ``metadata.create_all``:

* **Fresh install** creates every table through the baseline revision.
* **Existing databases** written before Alembic existed are detected (application
  tables present, no ``alembic_version``) and *stamped* with the baseline revision.
  Nothing is dropped, no table is created twice, and all rows stay untouched.
* **Failures stop the caller.** ``migrate`` raises :class:`MigrationError`, so service
  startup, the CLI and the deploy script abort instead of running against a schema
  they do not understand. There is no ``create_all`` fallback and no silent downgrade.
* A database whose recorded revision is unknown to this code base (for example a
  newer release) is refused rather than downgraded.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

#: Revision that describes the schema as it existed before migrations were introduced.
BASELINE_REVISION = "0001"

#: Application tables that predate Alembic; their presence without a revision row
#: means "this database is a pre-migration deployment".
LEGACY_TABLES = frozenset(
    {
        "corpora",
        "source_chapters",
        "generation_units",
        "jobs",
        "stage_attempts",
        "usage_records",
        "corpus_approvals",
        "practice_attempts",
        "vocabulary_marks",
        "vocabulary_reviews",
        "review_samples",
        "writing_evaluations",
    }
)

_VERSION_TABLE = "alembic_version"


class MigrationError(RuntimeError):
    """Raised when the schema cannot be brought to the expected revision."""


@dataclass(frozen=True)
class MigrationResult:
    """What ``migrate`` did, so callers can log it without guessing."""

    database_url: str
    from_revision: str | None
    to_revision: str
    head_revision: str
    stamped_baseline: bool
    changed: bool

    @property
    def up_to_date(self) -> bool:
        return not self.changed


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    """Build an Alembic config whose URL is supplied by the caller, never by the ini file."""

    ini_path = _repo_root() / "alembic.ini"
    if not ini_path.exists():  # pragma: no cover - only on a broken checkout
        raise MigrationError(f"找不到 Alembic 配置：{ini_path}")
    config = Config(str(ini_path))
    # Alembic resolves `script_location` against the current working directory, so pin it
    # to an absolute path: migrations must work from any cwd (deploy script, cron, tests).
    config.set_main_option("script_location", str(ini_path.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _head_revision(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:  # pragma: no cover - only if migrations/versions is emptied
        raise MigrationError("迁移脚本为空，无法确定目标版本")
    return head


def current_revision(engine: Engine | Connection) -> str | None:
    """Return the revision recorded in the database, or ``None`` when it has none."""

    if isinstance(engine, Connection):
        return MigrationContext.configure(engine).get_current_revision()
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _table_names(engine: Engine | Connection) -> set[str]:
    if isinstance(engine, Connection):
        return set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def looks_like_legacy_database(engine: Engine | Connection) -> bool:
    """True when the file holds application tables but no migration history."""

    tables = _table_names(engine)
    if _VERSION_TABLE in tables:
        return False
    return bool(tables & LEGACY_TABLES)


#: Schemas a pre-Alembic database may already have, newest first, with markers that prove
#: the shape (extra columns on ``jobs`` and/or extra tables). Detection matters because
#: stamping the wrong revision makes the next migration re-add existing objects.
LEGACY_SCHEMA_HINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("0003", (), ("web_sessions", "rate_limit_hits")),
    ("0002", ("worker_id", "idempotency_key", "attempts"), ()),
)


def detect_legacy_revision(engine: Engine | Connection) -> str:
    """Pick the revision that matches the schema of a pre-Alembic database."""

    tables = _table_names(engine)
    if _VERSION_TABLE in tables:
        return BASELINE_REVISION
    columns = _column_names(engine, "jobs")
    for revision, required_columns, required_tables in LEGACY_SCHEMA_HINTS:
        if set(required_columns) <= columns and set(required_tables) <= tables:
            return revision
    return BASELINE_REVISION


def _column_names(engine: Engine | Connection, table: str) -> set[str]:
    if isinstance(engine, Connection):
        return {column["name"] for column in inspect(engine).get_columns(table)}
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns(table)}


def read_schema_revision(path: str | Path) -> str | None:
    """Read the recorded revision straight from the file, creating nothing.

    ``--check`` must not turn an empty deployment into a database file, so this path
    opens the file read-only and reports ``None`` when it does not exist yet.
    """

    database = Path(path)
    if not database.exists() or database.stat().st_size == 0:
        return None
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        try:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None
    finally:
        connection.close()


def schema_status_for_path(path: str | Path) -> tuple[str | None, str]:
    """Read-only ``(current, head)`` for a database file that may not exist yet."""

    config = alembic_config(f"sqlite+pysqlite:///{Path(path)}")
    return read_schema_revision(path), _head_revision(config)


def migrate_engine(engine: Engine, *, stamp_legacy: bool = True) -> MigrationResult:
    """Upgrade ``engine`` to the newest revision, stamping pre-Alembic databases first."""

    url = engine.url.render_as_string(hide_password=False)
    config = alembic_config(url)
    head = _head_revision(config)

    with engine.begin() as connection:
        original = current_revision(connection)
        before = original
        stamped = False
        if before is None and stamp_legacy and looks_like_legacy_database(connection):
            # Pre-Alembic database: record the revision its schema already matches.
            detected = detect_legacy_revision(connection)
            _run_alembic(config, connection, lambda: command.stamp(config, detected))
            stamped = True
            before = detected

    if before == head:
        return MigrationResult(
            database_url=url,
            from_revision=original,
            to_revision=head,
            head_revision=head,
            stamped_baseline=stamped,
            changed=stamped,
        )

    # `engine.begin()` matters: the pysqlite driver commits DDL implicitly but rolls the
    # version row back when a plain connection closes, which would leave a database whose
    # schema moved without recording it.
    with engine.begin() as connection:
        _run_alembic(config, connection, lambda: command.upgrade(config, "head"))
        after = current_revision(connection)

    if after != head:  # pragma: no cover - Alembic reports this itself
        raise MigrationError(f"迁移后版本仍为 {after!r}，期望 {head!r}")

    return MigrationResult(
        database_url=url,
        from_revision=original,
        to_revision=head,
        head_revision=head,
        stamped_baseline=stamped,
        changed=True,
    )


def migrate_path(path: str | Path) -> MigrationResult:
    """Convenience wrapper for callers that only have a file path."""

    engine = create_engine(f"sqlite+pysqlite:///{Path(path)}", future=True)
    try:
        return migrate_engine(engine)
    finally:
        engine.dispose()


def schema_status(engine: Engine) -> tuple[str | None, str]:
    """Return ``(current_revision, head_revision)`` for readiness checks and the CLI."""

    config = alembic_config(engine.url.render_as_string(hide_password=False))
    return current_revision(engine), _head_revision(config)


def _run_alembic(config: Config, connection: Connection, action) -> None:
    """Run one Alembic command against an existing connection and classify failures."""

    config.attributes["connection"] = connection
    try:
        action()
    except (CommandError, SQLAlchemyError, OSError) as exc:
        raise MigrationError(f"数据库迁移失败，服务不会启动：{type(exc).__name__}: {exc}") from exc
    finally:
        config.attributes.pop("connection", None)
