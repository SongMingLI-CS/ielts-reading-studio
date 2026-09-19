"""Alembic environment for IELTS Learning Studio.

Three ways to point Alembic at a database, in priority order:

1. ``config.attributes["connection"]`` — used by ``app.storage.migrations`` so the
   application migrates the exact engine it already owns (no second connection, no
   chance of migrating a different file).
2. ``IELTS_DATABASE_URL`` — used by CI and operators.
3. ``config.yaml``'s ``database_path`` (default ``output/state.db``) — the local default.

Migration failures propagate as exceptions: the caller stops startup or deployment
rather than serving on an unknown schema.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.database import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    # Keep application loggers alive: fileConfig() would otherwise disable every logger
    # that already exists in this process (the web app logs crashes through them).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# `alembic.ini` uses %(here)s for both script_location and prepend_sys_path, so the
# repository root is on sys.path by the time this module imports `app`.
REPO_ROOT = (
    Path(config.config_file_name).resolve().parent
    if config.config_file_name
    else Path(__file__).resolve().parents[1]
)


def _resolve_url() -> str:
    override = os.environ.get("IELTS_DATABASE_URL")
    if override:
        return override
    from app.config import AppConfig

    config_path = REPO_ROOT / "config.yaml"
    if config_path.exists():
        return f"sqlite+pysqlite:///{AppConfig.load(config_path).database_path}"
    return f"sqlite+pysqlite:///{REPO_ROOT / 'output' / 'state.db'}"


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or _resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided = config.attributes.get("connection")
    if provided is not None:
        _run_with_connection(provided)
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url") or _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _run_with_connection(connection)
    finally:
        connectable.dispose()


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
