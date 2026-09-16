from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
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
    # Units may be indexed before their corpus record is persisted, so this is
    # deliberately an indexed identifier rather than an immediate FK.
    Column("corpus_id", String, nullable=False, index=True),
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
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
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

        return engine

    def create_schema(self) -> None:
        metadata.create_all(self.engine)
