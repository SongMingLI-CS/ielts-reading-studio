"""Migration tests: fresh installs, pre-Alembic databases, idempotency and drift.

Every test works on a throwaway SQLite file and never touches the network or a provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text

from app.config import AppConfig
from app.pipeline.service import ReadingStudioService
from app.storage.database import Database, metadata
from app.storage.migrations import (
    LEGACY_TABLES,
    MigrationError,
    looks_like_legacy_database,
    migrate_path,
    schema_status,
)

#: Rows written by a pre-migration release, keyed by the table that must keep them.
LEGACY_ROWS = (
    (
        "INSERT INTO corpora (id, name, source_path, source_hash, format, chapter_count, payload, created_at) "
        "VALUES ('corpus-1','Book','book.txt','hash','txt',2,'{}',CURRENT_TIMESTAMP)"
    ),
    "INSERT INTO jobs (id, corpus_id, status, payload) VALUES ('job-1','corpus-1','running','{}')",
    (
        "INSERT INTO generation_units (id, corpus_id, job_id, ordinal, status, payload) "
        "VALUES ('unit-1','corpus-1','job-1',1,'completed','{}')"
    ),
    (
        "INSERT INTO stage_attempts (id, unit_id, job_id, stage, attempt, status, payload) "
        "VALUES ('attempt-1','unit-1','job-1','author',1,'completed','{}')"
    ),
    (
        "INSERT INTO practice_attempts (id, unit_id, status, score, total, payload) "
        "VALUES ('practice-1','unit-1','submitted',7,10,'{}')"
    ),
    "INSERT INTO vocabulary_marks (word, status) VALUES ('conserve','saved')",
    "INSERT INTO vocabulary_reviews (word, box, due_at) VALUES ('conserve',2,CURRENT_TIMESTAMP)",
    "INSERT INTO review_samples (unit_id, status) VALUES ('unit-1','pending')",
    (
        "INSERT INTO writing_evaluations (id, task_type, model, prompt_version, payload) "
        "VALUES ('eval-1','task_2','deepseek-chat','p1','{}')"
    ),
)

TRACKED_TABLES = (
    "corpora",
    "jobs",
    "generation_units",
    "stage_attempts",
    "practice_attempts",
    "vocabulary_marks",
    "vocabulary_reviews",
    "review_samples",
    "writing_evaluations",
)


def make_legacy_database(path: Path) -> Database:
    """Rebuild the schema the way the pre-migration code did, then fill it with data."""

    database = Database(path)
    metadata.create_all(database.engine)
    with database.engine.begin() as connection:
        for statement in LEGACY_ROWS:
            connection.execute(text(statement))
    return database


def row_counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in TRACKED_TABLES
        }


def test_fresh_database_is_created_by_the_migration_chain(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    result = migrate_path(path)

    assert result.from_revision is None
    assert result.changed is True
    assert result.stamped_baseline is False
    assert result.to_revision == result.head_revision
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert LEGACY_TABLES <= tables
    assert "alembic_version" in tables


def test_legacy_database_is_stamped_and_keeps_every_row(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database = make_legacy_database(path)
    before = row_counts(database.engine)
    assert looks_like_legacy_database(database.engine) is True

    result = database.migrate()

    assert result.stamped_baseline is True
    assert result.from_revision is None
    assert result.to_revision == result.head_revision
    assert row_counts(database.engine) == before
    assert all(count > 0 for count in before.values())
    assert database.schema_revision() == result.head_revision
    assert looks_like_legacy_database(database.engine) is False


def test_migration_is_idempotent_on_fresh_and_legacy_databases(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.db"
    first = migrate_path(fresh)
    second = migrate_path(fresh)

    assert first.changed is True
    assert second.changed is False
    assert second.up_to_date is True
    assert second.from_revision == first.to_revision

    legacy_path = tmp_path / "legacy.db"
    legacy = make_legacy_database(legacy_path)
    stamped = legacy.migrate()
    again = legacy.migrate()

    assert stamped.stamped_baseline is True
    assert again.changed is False
    assert row_counts(legacy.engine)["corpora"] == 1


def test_database_with_an_unknown_future_revision_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    database.migrate()
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'deadbeef'"))

    with pytest.raises(MigrationError) as caught:
        database.migrate()

    assert "服务不会启动" in str(caught.value)
    # The database is left exactly as it was: no downgrade, no partial rewrite.
    assert database.schema_revision() == "deadbeef"


def test_failed_migration_raises_and_creates_no_application_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    database = Database(path)

    def explode(*_args, **_kwargs):
        raise CommandError("simulated migration failure")

    monkeypatch.setattr("app.storage.migrations.command.upgrade", explode)

    with pytest.raises(MigrationError) as caught:
        database.migrate()

    assert "数据库迁移失败" in str(caught.value)
    tables = set(inspect(database.engine).get_table_names())
    assert not (tables & LEGACY_TABLES)
    assert database.schema_revision() is None


def test_migrated_schema_matches_the_declared_metadata(tmp_path: Path) -> None:
    """Guard against model changes that never made it into a migration."""

    reference = Database(tmp_path / "reference.db")
    metadata.create_all(reference.engine)
    migrated_path = tmp_path / "migrated.db"
    migrate_path(migrated_path)
    migrated = create_engine(f"sqlite+pysqlite:///{migrated_path}")
    try:
        reference_inspector = inspect(reference.engine)
        migrated_inspector = inspect(migrated)
        expected_tables = set(reference_inspector.get_table_names())
        actual_tables = set(migrated_inspector.get_table_names()) - {"alembic_version"}
        assert actual_tables == expected_tables
        for table in sorted(expected_tables):
            assert {
                (column["name"], column["nullable"])
                for column in migrated_inspector.get_columns(table)
            } == {
                (column["name"], column["nullable"])
                for column in reference_inspector.get_columns(table)
            }, f"column drift in {table}"
            assert {
                index["name"] for index in migrated_inspector.get_indexes(table)
            } == {
                index["name"] for index in reference_inspector.get_indexes(table)
            }, f"index drift in {table}"
    finally:
        migrated.dispose()


def test_status_reports_current_and_head_revision(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database = Database(path)

    before = _status(database)
    database.migrate()

    assert before[0] is None
    assert isinstance(before[1], str) and before[1]
    assert _status(database) == (before[1], before[1])


def _status(database: Database) -> tuple[str | None, str]:
    return schema_status(database.engine)


def test_service_startup_migrates_the_database(tmp_path: Path) -> None:
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "state.db",
    )

    service = ReadingStudioService(config)

    assert service.migration.to_revision == service.migration.head_revision
    assert service.database.schema_revision() == service.migration.head_revision
