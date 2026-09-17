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
    Column("corpus_id", String, ForeignKey("corpora.id"), nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

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
        metadata.create_all(self.engine)
