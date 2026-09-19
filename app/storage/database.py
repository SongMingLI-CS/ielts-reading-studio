from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
)
from sqlalchemy.engine import Engine

from app.storage.migrations import MigrationResult, current_revision, migrate_engine

metadata = MetaData()

corpora = Table(
    "corpora",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("source_path", String, nullable=False),
    Column("source_hash", String, nullable=False, index=True),
    Column("format", String, nullable=False),
    Column("encoding", String),
    Column("chapter_count", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

source_chapters = Table(
    "source_chapters",
    metadata,
    Column("id", String, primary_key=True),
    Column("corpus_id", String, ForeignKey("corpora.id"), nullable=False, index=True),
    Column("ordinal", Integer, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    # Every job hangs off a corpus row: reading jobs use theirs, and non-reading work
    # (the context-novel component) uses a single system row the corpus list hides.
    Column("corpus_id", String, ForeignKey("corpora.id"), nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    # Queue metadata: what the job is, which worker owns it and until when.
    Column("kind", String, nullable=False, server_default="reading_generation"),
    Column("worker_id", String, index=True),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("error_code", String),
    Column("idempotency_key", String),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# One job per idempotency key: a double-clicked "start generation" cannot buy two runs.
Index(
    "uq_jobs_idempotency_key",
    jobs.c.idempotency_key,
    unique=True,
    sqlite_where=jobs.c.idempotency_key.is_not(None),
)

# Claiming scans the oldest queued job first.
Index("ix_jobs_status_created_at", jobs.c.status, jobs.c.created_at)

generation_units = Table(
    "generation_units",
    metadata,
    Column("id", String, primary_key=True),
    Column("corpus_id", String, ForeignKey("corpora.id"), nullable=False, index=True),
    Column("job_id", String, ForeignKey("jobs.id"), index=True),
    Column("ordinal", Integer, index=True),
    Column("status", String, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

stage_attempts = Table(
    "stage_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("unit_id", String, ForeignKey("generation_units.id"), nullable=False, index=True),
    Column("job_id", String, ForeignKey("jobs.id"), index=True),
    Column("stage", String, nullable=False, index=True),
    Column("attempt", Integer, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("cache_key", String, index=True),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("artifact_path", Text),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("unit_id", "stage", "attempt", name="uq_stage_attempt_identity"),
)

Index(
    "uq_stage_attempt_completed_cache_key",
    stage_attempts.c.cache_key,
    unique=True,
    sqlite_where=stage_attempts.c.status == "completed",
)

usage_records = Table(
    "usage_records",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("unit_id", String, ForeignKey("generation_units.id"), nullable=False, index=True),
    Column("job_id", String, ForeignKey("jobs.id"), index=True),
    Column("stage", String, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

corpus_approvals = Table(
    "corpus_approvals",
    metadata,
    Column("id", String, primary_key=True),
    Column("corpus_id", String, ForeignKey("corpora.id"), nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

practice_attempts = Table(
    "practice_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("unit_id", String, ForeignKey("generation_units.id"), nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("score", Integer),
    Column("total", Integer),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

vocabulary_marks = Table(
    "vocabulary_marks",
    metadata,
    Column("word", String, primary_key=True),
    # saved = collected into the vocabulary book, known = already learned
    Column("status", String, nullable=False, index=True),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

review_samples = Table(
    "review_samples",
    metadata,
    Column("unit_id", String, ForeignKey("generation_units.id"), primary_key=True),
    Column("job_id", String, index=True),
    Column("status", String, nullable=False, index=True),  # pending / passed / failed
    Column("decision", String),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

vocabulary_reviews = Table(
    "vocabulary_reviews",
    metadata,
    Column("word", String, primary_key=True),
    Column("box", Integer, nullable=False, server_default="1"),
    Column("due_at", DateTime(timezone=True), nullable=False, index=True),
    Column("seen", Integer, nullable=False, server_default="0"),
    Column("lapses", Integer, nullable=False, server_default="0"),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

writing_evaluations = Table(
    "writing_evaluations",
    metadata,
    Column("id", String, primary_key=True),
    Column("task_type", String, nullable=False, index=True),
    Column("model", String, nullable=False),
    Column("prompt_version", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


class Database:
    """SQLite database owner with a schema shared by all repository instances."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.engine = self._create_engine(self.path)

    @staticmethod
    def _create_engine(path: Path) -> Engine:
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite+pysqlite:///{path}",
            future=True,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _connection_record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")

        return engine

    def create_schema(self) -> None:
        """Historical name kept for existing callers; it now runs the migration chain.

        Creating tables with ``metadata.create_all`` used to be enough, but it silently
        diverges from the applied schema. Migrating instead means every code path
        (startup, CLI, tests) exercises the real schema and raises ``MigrationError``
        when the database cannot be upgraded safely.
        """
        self.migrate()

    def migrate(self) -> MigrationResult:
        """Bring this database to the newest schema revision (idempotent)."""

        return migrate_engine(self.engine)

    def schema_revision(self) -> str | None:
        """Revision recorded in this database, or ``None`` when it has never migrated."""

        return current_revision(self.engine)
