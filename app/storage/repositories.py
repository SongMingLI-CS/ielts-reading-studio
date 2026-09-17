from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from app.models import Corpus, GenerationUnit, SourceChapter, UnitStatus, UsageRecord

from .database import (
    Database,
    corpora,
    corpus_approvals,
    generation_units,
    jobs,
    practice_attempts,
    source_chapters,
    stage_attempts,
    usage_records,
)

RUNNING_RECOVERY_STATUSES: dict[UnitStatus, UnitStatus] = {
    UnitStatus.AUTHOR_GENERATING: UnitStatus.INDEXED,
    UnitStatus.PASSAGE_REVIEWING: UnitStatus.AUTHOR_REVISION_REQUIRED,
    UnitStatus.EXAMINER_GENERATING: UnitStatus.AUTHOR_REVISION_REQUIRED,
    UnitStatus.VALIDATING: UnitStatus.EXAMINER_REVISION_REQUIRED,
}


def _payload(model: Any) -> str:
    return json.dumps(_json_value(model), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _model(row: RowMapping, model_type: type[Corpus | GenerationUnit | SourceChapter | UsageRecord]):
    return model_type.model_validate_json(row["payload"])


@dataclass(frozen=True)
class StageAttempt:
    id: str
    unit_id: str
    job_id: str | None
    stage: str
    attempt: int
    status: str
    cache_key: str | None
    payload: Any
    artifact_path: str | None
    error: str | None


def _stage_attempt(row: RowMapping) -> StageAttempt:
    return StageAttempt(
        id=row["id"], unit_id=row["unit_id"], job_id=row["job_id"], stage=row["stage"],
        attempt=row["attempt"], status=row["status"], cache_key=row["cache_key"],
        payload=json.loads(row["payload"]), artifact_path=row["artifact_path"], error=row["error"],
    )


def _practice_attempt(row: RowMapping) -> dict[str, Any]:
    return {
        "id": row["id"],
        "unit_id": row["unit_id"],
        "status": row["status"],
        "score": row["score"],
        "total": row["total"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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

    def list_corpora(self) -> list[Corpus]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(select(corpora).order_by(corpora.c.created_at.desc())).mappings()
            return [_model(row, Corpus) for row in rows]

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

    def list_source_chapters(
        self,
        corpus_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[SourceChapter]:
        statement = (
            select(source_chapters)
            .where(source_chapters.c.corpus_id == corpus_id)
            .order_by(source_chapters.c.ordinal)
            .offset(max(0, offset))
        )
        if limit is not None:
            statement = statement.limit(max(0, limit))
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return [_model(row, SourceChapter) for row in rows]

    def list_jobs(self, corpus_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(jobs).order_by(jobs.c.created_at.desc())
        if corpus_id is not None:
            statement = statement.where(jobs.c.corpus_id == corpus_id)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return [
                {
                    "id": row["id"],
                    "corpus_id": row["corpus_id"],
                    "status": row["status"],
                    "payload": json.loads(row["payload"]),
                }
                for row in rows
            ]

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

    def add_units(self, units: Sequence[GenerationUnit], *, job_id: str | None = None) -> None:
        """Insert one batch of planned units in a single transaction."""
        if not units:
            return
        rows = [
            {
                "id": unit.id,
                "corpus_id": unit.corpus_id,
                "job_id": job_id,
                "ordinal": unit.ordinal,
                "status": unit.status.value,
                "payload": _payload(unit),
            }
            for unit in units
        ]
        with self.database.engine.begin() as connection:
            connection.execute(insert(generation_units), rows)

    def add_source_chapters(self, corpus_id: str, chapters: Sequence[SourceChapter]) -> None:
        """Insert one batch of chapters in a single transaction."""
        if not chapters:
            return
        rows = [
            {
                "id": chapter.id,
                "corpus_id": corpus_id,
                "ordinal": chapter.ordinal,
                "payload": _payload(chapter),
            }
            for chapter in chapters
        ]
        with self.database.engine.begin() as connection:
            connection.execute(insert(source_chapters), rows)

    def replace_corpus_index(
        self,
        corpus: Corpus,
        chapters: Sequence[SourceChapter],
        units: Sequence[GenerationUnit],
    ) -> None:
        """Atomically replace a not-yet-started corpus index after boundary edits."""
        chapter_rows = [
            {
                "id": chapter.id,
                "corpus_id": corpus.id,
                "ordinal": chapter.ordinal,
                "payload": _payload(chapter),
            }
            for chapter in chapters
        ]
        unit_rows = [
            {
                "id": unit.id,
                "corpus_id": unit.corpus_id,
                "job_id": None,
                "ordinal": unit.ordinal,
                "status": unit.status.value,
                "payload": _payload(unit),
            }
            for unit in units
        ]
        with self.database.engine.begin() as connection:
            connection.execute(delete(generation_units).where(generation_units.c.corpus_id == corpus.id))
            connection.execute(delete(source_chapters).where(source_chapters.c.corpus_id == corpus.id))
            connection.execute(
                update(corpora)
                .where(corpora.c.id == corpus.id)
                .values(chapter_count=corpus.chapter_count, payload=_payload(corpus))
            )
            if chapter_rows:
                connection.execute(insert(source_chapters), chapter_rows)
            if unit_rows:
                connection.execute(insert(generation_units), unit_rows)

    def count_source_chapters(self, corpus_id: str) -> int:
        with self.database.engine.connect() as connection:
            return connection.execute(
                select(func.count()).select_from(source_chapters).where(source_chapters.c.corpus_id == corpus_id)
            ).scalar_one()

    def list_units(self, corpus_id: str) -> list[GenerationUnit]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(generation_units)
                .where(generation_units.c.corpus_id == corpus_id)
                .order_by(generation_units.c.ordinal)
            ).mappings()
            return [_model(row, GenerationUnit) for row in rows]

    def list_job_units(self, job_id: str) -> list[GenerationUnit]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(generation_units)
                .where(generation_units.c.job_id == job_id)
                .order_by(generation_units.c.ordinal)
            ).mappings()
            return [_model(row, GenerationUnit) for row in rows]

    def assign_units_to_job(self, unit_ids: Sequence[str], job_id: str) -> int:
        if not unit_ids:
            return 0
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(generation_units)
                .where(generation_units.c.id.in_(unit_ids))
                .values(job_id=job_id, updated_at=func.now())
            )
        return result.rowcount

    def get_unit(self, unit_id: str) -> GenerationUnit | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(generation_units).where(generation_units.c.id == unit_id)).mappings().one_or_none()
        return _model(row, GenerationUnit) if row is not None else None

    def update_indexed_unit(self, unit: GenerationUnit) -> bool:
        """Replace immutable generation settings only before a unit has started."""
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(generation_units)
                .where(
                    generation_units.c.id == unit.id,
                    generation_units.c.status == UnitStatus.INDEXED.value,
                )
                .values(payload=_payload(unit), updated_at=func.now())
            )
        return result.rowcount == 1

    def transition(self, unit_id: str, expected: UnitStatus, target: UnitStatus) -> bool:
        """Atomically move a unit only when it is still in ``expected`` status."""
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(generation_units)
                .where(
                    generation_units.c.id == unit_id,
                    generation_units.c.status == expected.value,
                )
                .values(
                    status=target.value,
                    payload=func.json_set(generation_units.c.payload, "$.status", target.value),
                    updated_at=func.now(),
                )
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

    def save_practice_attempt(
        self,
        attempt_id: str,
        unit_id: str,
        *,
        status: str,
        payload: dict[str, Any],
        score: int | None = None,
        total: int | None = None,
    ) -> None:
        values = {
            "id": attempt_id,
            "unit_id": unit_id,
            "status": status,
            "score": score,
            "total": total,
            "payload": _payload(payload),
        }
        statement = sqlite_insert(practice_attempts).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[practice_attempts.c.id],
            set_={
                "status": status,
                "score": score,
                "total": total,
                "payload": _payload(payload),
                "updated_at": func.now(),
            },
        )
        with self.database.engine.begin() as connection:
            connection.execute(statement)

    def get_practice_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(practice_attempts).where(practice_attempts.c.id == attempt_id)
            ).mappings().one_or_none()
        return _practice_attempt(row) if row is not None else None

    def list_practice_attempts(self, unit_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(practice_attempts).order_by(practice_attempts.c.updated_at.desc())
        if unit_id is not None:
            statement = statement.where(practice_attempts.c.unit_id == unit_id)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            return [_practice_attempt(row) for row in rows]

    def create_job(self, job_id: str, corpus_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(insert(jobs).values(
                id=job_id, corpus_id=corpus_id, status=status, payload=_payload(payload or {}),
            ))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one_or_none()
        if row is None:
            return None
        return {"id": row["id"], "corpus_id": row["corpus_id"], "status": row["status"], "payload": json.loads(row["payload"])}

    def update_job(self, job_id: str, *, status: str | None = None, payload: dict[str, Any] | None = None) -> bool:
        values: dict[str, Any] = {"updated_at": func.now()}
        if status is not None:
            values["status"] = status
        if payload is not None:
            values["payload"] = _payload(payload)
        with self.database.engine.begin() as connection:
            result = connection.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
        return result.rowcount == 1

    def record_corpus_approval(
        self, approval_id: str, corpus_id: str, status: str, payload: dict[str, Any] | None = None,
    ) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(insert(corpus_approvals).values(
                id=approval_id, corpus_id=corpus_id, status=status, payload=_payload(payload or {}),
            ))

    def get_corpus_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(select(corpus_approvals).where(corpus_approvals.c.id == approval_id)).mappings().one_or_none()
        if row is None:
            return None
        return {"id": row["id"], "corpus_id": row["corpus_id"], "status": row["status"], "payload": json.loads(row["payload"])}

    def get_latest_corpus_approval(self, corpus_id: str) -> dict[str, Any] | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(corpus_approvals)
                .where(corpus_approvals.c.corpus_id == corpus_id)
                .order_by(corpus_approvals.c.created_at.desc())
                .limit(1)
            ).mappings().one_or_none()
        if row is None:
            return None
        return {
            "id": row["id"],
            "corpus_id": row["corpus_id"],
            "status": row["status"],
            "payload": json.loads(row["payload"]),
        }

    def create_stage_attempt(
        self,
        unit_id: str,
        stage: str,
        attempt: int,
        *,
        job_id: str | None = None,
        cache_key: str | None = None,
        payload: dict[str, Any] | BaseModel | None = None,
    ) -> None:
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        with self.database.engine.begin() as connection:
            connection.execute(insert(stage_attempts).values(
                id=f"{unit_id}:{stage}:{attempt}", unit_id=unit_id, job_id=job_id,
                stage=stage, attempt=attempt, status="running", cache_key=cache_key,
                payload=_payload(payload or {}),
            ))

    def get_stage_attempt(self, unit_id: str, stage: str, attempt: int) -> StageAttempt | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(stage_attempts).where(
                    stage_attempts.c.unit_id == unit_id,
                    stage_attempts.c.stage == stage,
                    stage_attempts.c.attempt == attempt,
                )
            ).mappings().one_or_none()
        return _stage_attempt(row) if row is not None else None

    def next_stage_attempt_number(self, unit_id: str, stage: str) -> int:
        with self.database.engine.connect() as connection:
            maximum = connection.execute(
                select(func.max(stage_attempts.c.attempt)).where(
                    stage_attempts.c.unit_id == unit_id,
                    stage_attempts.c.stage == stage,
                )
            ).scalar_one()
        return int(maximum or 0) + 1

    def corpus_has_stage_attempts(self, corpus_id: str) -> bool:
        with self.database.engine.connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(
                    stage_attempts.join(
                        generation_units,
                        stage_attempts.c.unit_id == generation_units.c.id,
                    )
                )
                .where(generation_units.c.corpus_id == corpus_id)
            ).scalar_one()
        return bool(count)

    def update_stage_attempt(
        self,
        unit_id: str,
        stage: str,
        attempt: int,
        *,
        cache_key: str | None = None,
        payload: dict[str, Any] | BaseModel | None = None,
        artifact_path: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update mutable metadata while an attempt remains resumable."""
        values: dict[str, Any] = {"updated_at": func.now()}
        if cache_key is not None:
            values["cache_key"] = cache_key
        if payload is not None:
            values["payload"] = _payload(payload)
        if artifact_path is not None:
            values["artifact_path"] = artifact_path
        if error is not None:
            values["error"] = error
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(stage_attempts)
                .where(
                    stage_attempts.c.unit_id == unit_id,
                    stage_attempts.c.stage == stage,
                    stage_attempts.c.attempt == attempt,
                    stage_attempts.c.status == "running",
                )
                .values(**values)
            )
        return result.rowcount == 1

    def get_cached_stage(self, cache_key: str) -> StageAttempt | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(
                select(stage_attempts)
                .where(stage_attempts.c.cache_key == cache_key, stage_attempts.c.status == "completed")
            ).mappings().one_or_none()
        return _stage_attempt(row) if row is not None else None

    def complete_stage_attempt(
        self,
        unit_id: str,
        stage: str,
        attempt: int,
        *,
        artifact_path: str,
        payload: dict[str, Any] | BaseModel,
    ) -> bool:
        """Commit completion metadata after the caller has atomically saved its artifact."""
        try:
            with self.database.engine.begin() as connection:
                result = connection.execute(
                    update(stage_attempts)
                    .where(
                        stage_attempts.c.unit_id == unit_id,
                        stage_attempts.c.stage == stage,
                        stage_attempts.c.attempt == attempt,
                        stage_attempts.c.status == "running",
                    )
                    .values(
                        status="completed", payload=_payload(payload), artifact_path=artifact_path,
                        error=None, updated_at=func.now(),
                    )
                )
                return result.rowcount == 1
        except IntegrityError:
            return False

    def reuse_stage_attempt(
        self,
        unit_id: str,
        stage: str,
        attempt: int,
        *,
        artifact_path: str | None,
        payload: dict[str, Any] | BaseModel,
    ) -> bool:
        """Finish a losing cache race without duplicating the unique cache key."""
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(stage_attempts)
                .where(
                    stage_attempts.c.unit_id == unit_id,
                    stage_attempts.c.stage == stage,
                    stage_attempts.c.attempt == attempt,
                    stage_attempts.c.status == "running",
                )
                .values(
                    status="completed",
                    cache_key=None,
                    payload=_payload(payload),
                    artifact_path=artifact_path,
                    error="reused_completed_cache",
                    updated_at=func.now(),
                )
            )
        return result.rowcount == 1

    def fail_stage_attempt(
        self,
        unit_id: str,
        stage: str,
        attempt: int,
        *,
        error: str,
        payload: dict[str, Any] | BaseModel | None = None,
    ) -> bool:
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(stage_attempts)
                .where(
                    stage_attempts.c.unit_id == unit_id,
                    stage_attempts.c.stage == stage,
                    stage_attempts.c.attempt == attempt,
                    stage_attempts.c.status == "running",
                )
                .values(
                    status="failed", error=error,
                    payload=_payload(payload or {}), updated_at=func.now(),
                )
            )
        return result.rowcount == 1
