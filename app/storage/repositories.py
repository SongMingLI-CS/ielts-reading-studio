from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping

from app.models import Corpus, GenerationUnit, SourceChapter, UnitStatus, UsageRecord

from .database import (
    Database,
    corpora,
    generation_units,
    source_chapters,
    usage_records,
)


RUNNING_RECOVERY_STATUSES: dict[UnitStatus, UnitStatus] = {
    UnitStatus.AUTHOR_GENERATING: UnitStatus.INDEXED,
    UnitStatus.PASSAGE_REVIEWING: UnitStatus.AUTHOR_REVISION_REQUIRED,
    UnitStatus.EXAMINER_GENERATING: UnitStatus.AUTHOR_REVISION_REQUIRED,
    UnitStatus.VALIDATING: UnitStatus.EXAMINER_REVISION_REQUIRED,
}


def _payload(model: Any) -> str:
    return model.model_dump_json()


def _model(row: RowMapping, model_type: type[Corpus] | type[GenerationUnit] | type[SourceChapter] | type[UsageRecord]):
    return model_type.model_validate_json(row["payload"])


class Repository:
    """Transactional access to resumable corpus and generation state."""

    def __init__(self, database: Database):
        self.database = database

    def add_corpus(self, corpus: Corpus) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(corpora).values(
                    id=corpus.id,
                    name=corpus.name,
                    source_path=corpus.source_path,
                    source_hash=corpus.source_hash,
                    format=corpus.format,
                    encoding=corpus.encoding,
                    chapter_count=corpus.chapter_count,
                    payload=_payload(corpus),
                    created_at=corpus.created_at,
                )
            )

    def get_corpus(self, corpus_id: str) -> Corpus | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(corpora).where(corpora.c.id == corpus_id)).mappings().one_or_none()
        return _model(row, Corpus) if row is not None else None

    def add_source_chapter(self, corpus_id: str, chapter: SourceChapter) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(source_chapters).values(
                    id=chapter.id,
                    corpus_id=corpus_id,
                    ordinal=chapter.ordinal,
                    payload=_payload(chapter),
                )
            )

    def get_source_chapter(self, chapter_id: str) -> SourceChapter | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(source_chapters).where(source_chapters.c.id == chapter_id)).mappings().one_or_none()
        return _model(row, SourceChapter) if row is not None else None

    def add_unit(self, unit: GenerationUnit, *, job_id: str | None = None, ordinal: int | None = None) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(generation_units).values(
                    id=unit.id,
                    corpus_id=unit.corpus_id,
                    job_id=job_id,
                    ordinal=ordinal,
                    status=unit.status.value,
                    payload=_payload(unit),
                )
            )

    def get_unit(self, unit_id: str) -> GenerationUnit | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(generation_units).where(generation_units.c.id == unit_id)).mappings().one_or_none()
        return _model(row, GenerationUnit) if row is not None else None

    def transition(self, unit_id: str, expected: UnitStatus, target: UnitStatus) -> bool:
        """Atomically move a unit only when it is still in ``expected`` status."""
        with self.database.engine.begin() as connection:
            row = connection.execute(
                select(generation_units.c.payload).where(
                    generation_units.c.id == unit_id,
                    generation_units.c.status == expected.value,
                )
            ).mappings().one_or_none()
            if row is None:
                return False
            unit = GenerationUnit.model_validate_json(row["payload"])
            updated = unit.model_copy(update={"status": target})
            result = connection.execute(
                update(generation_units)
                .where(
                    generation_units.c.id == unit_id,
                    generation_units.c.status == expected.value,
                )
                .values(status=target.value, payload=_payload(updated), updated_at=func.now())
            )
            return result.rowcount == 1

    def recover_interrupted_units(self) -> list[str]:
        """Return in-flight units to the last safe, resumable state."""
        recovered: list[str] = []
        with self.database.engine.begin() as connection:
            for interrupted, resumable in RUNNING_RECOVERY_STATUSES.items():
                rows = connection.execute(
                    select(generation_units.c.id, generation_units.c.payload)
                    .where(generation_units.c.status == interrupted.value)
                    .order_by(generation_units.c.id)
                ).mappings()
                for row in rows:
                    unit = GenerationUnit.model_validate_json(row["payload"])
                    updated = unit.model_copy(update={"status": resumable})
                    result = connection.execute(
                        update(generation_units)
                        .where(
                            generation_units.c.id == row["id"],
                            generation_units.c.status == interrupted.value,
                        )
                        .values(status=resumable.value, payload=_payload(updated), updated_at=func.now())
                    )
                    if result.rowcount == 1:
                        recovered.append(row["id"])
        return sorted(recovered)

    def add_usage_record(self, unit_id: str, usage: UsageRecord, *, job_id: str | None = None) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(usage_records).values(
                    unit_id=unit_id,
                    job_id=job_id,
                    stage=usage.stage,
                    payload=_payload(usage),
                )
            )

    def list_usage_records(self, unit_id: str) -> list[UsageRecord]:
        with self.database.engine.connect() as connection:
            rows: Iterable[RowMapping] = connection.execute(
                select(usage_records)
                .where(usage_records.c.unit_id == unit_id)
                .order_by(usage_records.c.id)
            ).mappings()
            return [_model(row, UsageRecord) for row in rows]
