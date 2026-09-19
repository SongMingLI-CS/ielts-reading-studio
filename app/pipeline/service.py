from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.agents.author import AuthorAgent
from app.agents.deepseek import DeepSeekProvider
from app.agents.examiner import ExaminerAgent
from app.config import AppConfig
from app.exporters.service import ExportService
from app.models import (
    Corpus,
    Difficulty,
    GenerationUnit,
    QuestionType,
    ReadingPackage,
    UnitStatus,
)
from app.planning.boundaries import BoundaryEdit, apply_boundary_edits
from app.planning.estimate import estimate_run
from app.planning.importer import CorpusImporter
from app.planning.units import (
    build_manifest,
    default_question_types,
    plan_units,
    unit_source_text,
)
from app.security.budget import check_batch_cost, enforce
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import RUNNING_RECOVERY_STATUSES, Repository
from app.validators.similarity import QuestionIndex
from app.writing.models import WritingEvaluationRequest, WritingEvaluationResponse
from app.writing.service import WritingEvaluationService

from .batch_runner import BatchRunner
from .queue import JobQueue, QueueLimitError
from .unit_runner import UnitRunner


class ReadingStudioService:
    """Application-facing facade shared by the CLI and local web UI."""

    def __init__(
        self,
        config: AppConfig,
        *,
        provider: Any | None = None,
    ) -> None:
        self.config = config
        self.database = Database(config.database_path)
        # Migration must succeed before anything else is wired: a half-migrated schema
        # is not something the rest of the application can reason about.
        self.migration = self.database.migrate()
        self.repository = Repository(self.database)
        # Shared with the worker process: the web side only enqueues, the worker claims.
        self.queue = JobQueue(self.database, lease_seconds=config.job_lease_seconds)
        self.store = ArtifactStore(config.output_dir)
        self.importer = CorpusImporter(config, repository=self.repository, store=self.store)
        self.exporter = ExportService()
        self._provider = provider
        self._question_index: QuestionIndex | None = None
        self._question_index_fingerprint: tuple[str, ...] | None = None

    def evaluate_writing(
        self, submission: WritingEvaluationRequest
    ) -> WritingEvaluationResponse:
        provider = self._provider
        if provider is None:
            provider = DeepSeekProvider(self.config)
            self._provider = provider
        return WritingEvaluationService(provider, self.config, self.repository).evaluate(
            submission
        )

    def import_source(
        self,
        path: str | Path,
        *,
        difficulty: Difficulty = Difficulty.STANDARD,
        question_types: list[QuestionType] | None = None,
    ):
        return self.importer.import_source(
            Path(path),
            difficulty=difficulty,
            question_types=question_types,
        )

    def source_text_for_unit(self, unit: GenerationUnit) -> str:
        chapters = {
            chapter_id: self.repository.get_source_chapter(chapter_id)
            for chapter_id in unit.source_chapter_ids
        }
        if any(chapter is None for chapter in chapters.values()):
            raise KeyError(f"Source chapters missing for unit {unit.id}")
        return unit_source_text(unit, chapters)  # type: ignore[arg-type]

    def build_question_index(self, *, refresh: bool = False) -> QuestionIndex:
        """把已完成篇目的题干收进索引，供跨篇去重使用。

        读一次全部 Package 的成本随篇数线性增长，所以索引按"已完成篇目 ID 集合"
        缓存：集合没变就直接复用，变了（新篇目完成）才重建。
        """
        units = [
            unit
            for corpus in self.repository.list_corpora()
            for unit in self.repository.list_units(corpus.id)
            if unit.status == UnitStatus.COMPLETED
        ]
        fingerprint = tuple(sorted(unit.id for unit in units))
        if (
            not refresh
            and self._question_index is not None
            and self._question_index_fingerprint == fingerprint
        ):
            return self._question_index
        index = QuestionIndex(threshold=self.config.question_duplicate_threshold)
        for unit in units:
            try:
                index.add_package(self.load_package(unit.id))
            except (FileNotFoundError, ValueError):
                continue
        self._question_index = index
        self._question_index_fingerprint = fingerprint
        return index

    def duplicate_report(
        self, *, threshold: float | None = None
    ) -> list[dict[str, Any]]:
        """已完成篇目之间的近似重复题，供审阅页展示。"""
        index = self.build_question_index(refresh=True)
        pairs = index.duplicate_pairs(
            threshold=self.config.question_report_threshold
            if threshold is None
            else threshold
        )
        return [
            {
                "score": round(pair.score * 100),
                "left": pair.left.as_dict(),
                "right": pair.right.as_dict(),
            }
            for pair in pairs
        ]

    def unit_runner(self, question_index: QuestionIndex | None = None) -> UnitRunner:
        provider = self._provider
        if provider is None:
            provider = DeepSeekProvider(self.config)
            self._provider = provider
        return UnitRunner(
            config=self.config,
            repository=self.repository,
            store=self.store,
            author=AuthorAgent(provider, self.config),
            examiner=ExaminerAgent(provider, self.config),
            source_text_loader=self.source_text_for_unit,
            question_index=question_index,
        )

    def inspect_corpus(self, corpus_id: str) -> dict[str, Any]:
        corpus = self.repository.get_corpus(corpus_id)
        if corpus is None:
            raise KeyError(f"Unknown corpus: {corpus_id}")
        units = self.repository.list_units(corpus_id)
        return {
            "corpus": corpus,
            "unit_count": len(units),
            "statuses": _counts(unit.status.value for unit in units),
            "approval": self.repository.get_latest_corpus_approval(corpus_id),
        }

    def apply_boundary_overrides(
        self,
        corpus_id: str,
        edits: list[BoundaryEdit],
    ):
        corpus = self.repository.get_corpus(corpus_id)
        if corpus is None:
            raise KeyError(f"Unknown corpus: {corpus_id}")
        units = self.repository.list_units(corpus_id)
        if any(unit.status != UnitStatus.INDEXED for unit in units):
            raise PermissionError("Chapter boundaries are frozen after generation starts")
        if (
            self.repository.list_jobs(corpus_id)
            or self.repository.get_latest_corpus_approval(corpus_id)
            or self.repository.corpus_has_stage_attempts(corpus_id)
        ):
            raise PermissionError("Chapter boundaries are frozen after job creation or sample approval")
        chapters = self.repository.list_source_chapters(corpus_id)
        changed_chapters = apply_boundary_edits(chapters, edits)
        difficulty = units[0].difficulty if units else Difficulty.STANDARD
        question_types = units[0].question_types if units else default_question_types(difficulty)
        changed_units = plan_units(
            changed_chapters,
            self.config,
            difficulty,
            question_types,
            corpus_id=corpus_id,
        )
        changed_corpus = corpus.model_copy(update={"chapter_count": len(changed_chapters)})
        previous = self.importer.load_manifest(corpus)
        manifest = build_manifest(
            changed_corpus,
            changed_chapters,
            changed_units,
            confidence=previous.confidence if previous else 1.0,
            diagnostics=previous.diagnostics if previous else [],
            candidate_chapters=[],
        )
        self.repository.replace_corpus_index(changed_corpus, changed_chapters, changed_units)
        relative = self.importer.manifest_relative_path(changed_corpus)
        self.store.write_json(relative, manifest)
        self.store.write_json(
            relative.parent / "boundary-overrides.json",
            {"edits": [edit.model_dump(mode="json") for edit in edits]},
        )
        return manifest

    def selected_units(self, corpus_id: str, ordinals: list[int] | None = None) -> list[GenerationUnit]:
        units = self.repository.list_units(corpus_id)
        if self.repository.get_corpus(corpus_id) is None:
            raise KeyError(f"Unknown corpus: {corpus_id}")
        if ordinals is None:
            return units
        requested = set(ordinals)
        selected = [unit for unit in units if unit.ordinal in requested]
        missing = sorted(requested - {unit.ordinal for unit in selected})
        if missing:
            raise ValueError(f"Unknown unit ordinals: {missing}")
        return selected

    def estimate_corpus(self, corpus_id: str, ordinals: list[int] | None = None):
        return estimate_run(
            self.selected_units(corpus_id, ordinals),
            self.config.author_revision_limit,
            self.config.examiner_revision_limit,
        )

    def corpus_deletion_impact(self, corpus_id: str) -> dict[str, Any]:
        """Everything a corpus deletion would remove, so it can be reviewed first."""
        corpus = self.repository.get_corpus(corpus_id)
        if corpus is None:
            raise KeyError(f"Unknown corpus: {corpus_id}")
        units = self.repository.list_units(corpus_id)
        unit_ids = {unit.id for unit in units}
        statuses = _counts(unit.status.value for unit in units)
        jobs = self.repository.list_jobs(corpus_id)
        attempts = [
            attempt
            for attempt in self.repository.list_practice_attempts()
            if attempt["unit_id"] in unit_ids
        ]
        artifacts = [path for path in self._corpus_artifact_paths(corpus, unit_ids) if path.exists()]
        return {
            "corpus": corpus,
            "statuses": statuses,
            "units": len(units),
            "chapters": self.repository.count_source_chapters(corpus_id),
            "completed": statuses.get(UnitStatus.COMPLETED.value, 0),
            "needs_review": statuses.get(UnitStatus.NEEDS_REVIEW.value, 0),
            "failed": statuses.get(UnitStatus.FAILED.value, 0),
            "running": sum(
                statuses.get(status.value, 0) for status in RUNNING_RECOVERY_STATUSES
            ),
            "practice_attempts": len(attempts),
            "graded_attempts": sum(
                1 for attempt in attempts if attempt["status"] == "submitted"
            ),
            "jobs": len(jobs),
            "active_jobs": [
                job["id"] for job in jobs if job["status"] in {"queued", "running"}
            ],
            "approved": self.repository.get_latest_corpus_approval(corpus_id) is not None,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "artifact_bytes": sum(_path_size(path) for path in artifacts),
        }

    def delete_corpus(self, corpus_id: str) -> dict[str, Any]:
        """Delete a corpus, its rows and its artifacts. Callers confirm first."""
        impact = self.corpus_deletion_impact(corpus_id)
        if impact["running"] or impact["active_jobs"]:
            raise PermissionError(
                "Corpus has running units or an active job; pause or cancel it first"
            )
        units = self.repository.list_units(corpus_id)
        removed = [
            path
            for path in self._corpus_artifact_paths(
                impact["corpus"], {unit.id for unit in units}
            )
            if path.exists()
        ]
        for path in removed:
            _remove_path(path)
        counts = self.repository.delete_corpus(corpus_id)
        return {"counts": counts, "removed": removed, "impact": impact}

    def _corpus_artifact_paths(self, corpus: Corpus, unit_ids: set[str]) -> list[Path]:
        manifest = self.importer.locate_manifest(corpus)
        if manifest is None:
            manifest_dir = self.importer.manifest_path(corpus)
        else:
            manifest_dir = manifest
        paths: list[Path] = [manifest_dir.parent, self.config.input_dir / corpus.id]
        for unit_id in sorted(unit_ids):
            paths.extend(
                [
                    self.store.root / "packages" / f"{unit_id}.json",
                    self.store.root / "stage_payloads" / unit_id,
                    self.store.root / "raw_responses" / unit_id,
                    self.store.root / "reports" / unit_id,
                    self.store.root / "failed" / f"{unit_id}.json",
                ]
            )
        for job in self.repository.list_jobs(corpus.id):
            paths.append(self.store.root / "reports" / f"{job['id']}-summary.json")
        return paths

    def generate_sample(
        self,
        corpus_id: str,
        difficulty: Difficulty = Difficulty.STANDARD,
        question_types: list[QuestionType] | None = None,
    ):
        units = self.selected_units(corpus_id)
        if not units:
            raise ValueError("Corpus has no reliable generation units")
        selected_types = question_types or default_question_types(difficulty)
        unit = self.configure_units(corpus_id, [units[0].ordinal], difficulty, selected_types)[0]
        return self.unit_runner(self.build_question_index()).run(unit.id)

    def configure_units(
        self,
        corpus_id: str,
        ordinals: list[int] | None,
        difficulty: Difficulty,
        question_types: list[QuestionType],
    ) -> list[GenerationUnit]:
        if len(question_types) != 3 or len(set(question_types)) != 3:
            raise ValueError("Exactly three distinct question types are required")
        configured: list[GenerationUnit] = []
        for unit in self.selected_units(corpus_id, ordinals):
            if unit.difficulty == difficulty and unit.question_types == question_types:
                configured.append(unit)
                continue
            if unit.status != UnitStatus.INDEXED:
                raise ValueError(f"Unit {unit.ordinal} has started and its generation settings are frozen")
            changed = unit.model_copy(
                update={
                    "difficulty": difficulty,
                    "question_types": list(question_types),
                    "config_snapshot": {
                        **unit.config_snapshot,
                        "difficulty": difficulty.value,
                        "question_types": [value.value for value in question_types],
                    },
                }
            )
            if not self.repository.update_indexed_unit(changed):
                raise RuntimeError(f"Could not configure unit {unit.id}")
            configured.append(changed)
        return configured

    def approve_sample(self, corpus_id: str, unit_id: str) -> dict[str, Any]:
        unit = self.repository.get_unit(unit_id)
        if unit is None or unit.corpus_id != corpus_id:
            raise ValueError("Sample unit does not belong to the corpus")
        if unit.status != UnitStatus.COMPLETED:
            raise ValueError("Sample unit must be completed before approval")
        approval_id = f"{corpus_id}:{unit_id}"
        payload = {
            "unit_id": unit_id,
            "approved": True,
            "difficulty": unit.difficulty.value,
            "question_types": [value.value for value in unit.question_types],
        }
        existing = self.repository.get_corpus_approval(approval_id)
        if existing is None:
            self.repository.record_corpus_approval(approval_id, corpus_id, "approved", payload)
        return {"id": approval_id, "corpus_id": corpus_id, "status": "approved", "payload": payload}

    def is_corpus_approved(self, corpus_id: str) -> bool:
        approval = self.repository.get_latest_corpus_approval(corpus_id)
        return approval is not None and approval["status"] == "approved"

    def create_job(
        self,
        corpus_id: str,
        ordinals: list[int] | None = None,
        *,
        batch_size: int | None = None,
        concurrency: int | None = None,
        difficulty: Difficulty | None = None,
        question_types: list[QuestionType] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            # A double-clicked "start" must not buy two batches of model calls.
            existing = self.queue.find_active_by_key(idempotency_key)
            if existing is not None:
                return self.repository.get_job(existing.id)
        running = self.queue.running_count()
        if running >= self.config.max_active_jobs:
            raise QueueLimitError(
                f"已有 {running} 个排队或运行中的任务（上限 {self.config.max_active_jobs}）；"
                "请等待完成，或先暂停/取消旧任务再提交"
            )
        approval = self.repository.get_latest_corpus_approval(corpus_id)
        if approval is None or approval["status"] != "approved":
            raise PermissionError("A completed sample must be explicitly approved before batch generation")
        approved_difficulty = Difficulty(approval["payload"]["difficulty"])
        approved_types = [QuestionType(value) for value in approval["payload"]["question_types"]]
        selected_difficulty = difficulty or approved_difficulty
        selected_types = question_types or approved_types
        if selected_difficulty != approved_difficulty or selected_types != approved_types:
            raise PermissionError("Batch settings must match the explicitly approved sample")
        units = [
            unit
            for unit in self.configure_units(
                corpus_id,
                ordinals,
                selected_difficulty,
                selected_types,
            )
            if unit.status != UnitStatus.COMPLETED
        ]
        # Refuse a batch whose offline estimate already exceeds the run budget: a job that
        # cannot finish within the configured cost must not occupy the queue.
        estimate = estimate_run(
            units,
            self.config.author_revision_limit,
            self.config.examiner_revision_limit,
        )
        enforce(
            check_batch_cost(
                estimated_tokens=estimate.maximum_tokens,
                limit=self.config.max_estimated_tokens_per_run,
                units=len(units),
            )
        )
        job_id = str(uuid4())
        selected_batch_size = batch_size or self.config.batch_size
        selected_concurrency = concurrency or self.config.concurrency
        if not 1 <= selected_batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not 1 <= selected_concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")
        payload = {
            "unit_ids": [unit.id for unit in units],
            "ordinals": [unit.ordinal for unit in units],
            "batch_size": selected_batch_size,
            "concurrency": selected_concurrency,
            "difficulty": selected_difficulty.value,
            "question_types": [value.value for value in selected_types],
        }
        try:
            self.repository.create_job(
                job_id,
                corpus_id,
                "queued",
                payload,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            # Another request with the same key won the race; reuse its job.
            if not idempotency_key:
                raise
            existing = self.queue.find_by_idempotency_key(idempotency_key)
            if existing is None:  # pragma: no cover - the winner is committed
                raise
            return self.repository.get_job(existing.id)
        self.repository.assign_units_to_job(payload["unit_ids"], job_id)
        return self.repository.get_job(job_id)
    def run_job(self, job_id: str, *, question_index: QuestionIndex | None = None):
        summary = BatchRunner(
            self.config,
            self.repository,
            self.store,
            self.unit_runner(
                question_index
                if question_index is not None
                else self.build_question_index()
            ),
        ).run(job_id)
        # 一批跑完后自动抽样，把"人工看一眼"变成批任务的最后一道工序。
        self.sample_job_units(job_id)
        return summary

    def sample_job_units(
        self, job_id: str, *, size: int | None = None, force: bool = False
    ) -> list[dict[str, Any]]:
        """从一批的已完成单元里抽几篇进审阅队列。

        抽样用 job_id 做随机种子：同一次抽样结果稳定，重复调用不会换样本；
        ``force=True`` 才重抽（网页上的「重新抽样」按钮）。已经人工审过的单元
        不会被重新抽出来，避免把做过的工作覆盖掉。
        """
        units = [
            unit
            for unit in self.repository.list_job_units(job_id)
            if unit.status == UnitStatus.COMPLETED
        ]
        if not units:
            return []
        if size is None:
            ratio = max(0.0, min(1.0, self.config.review_sample_rate))
            size = max(self.config.review_sample_min, round(len(units) * ratio))
        size = max(0, min(size, len(units)))
        if size == 0:
            return []
        decided = {
            row["unit_id"]
            for row in self.repository.list_review_samples()
            if row["status"] != "pending"
        }
        pending = {
            row["unit_id"]
            for row in self.repository.list_review_samples()
            if row["status"] == "pending"
        }
        unit_ids = {unit.id for unit in units}
        if force:
            candidates = list(units)
            missing = size
        else:
            # 已经在队列里等人工看的不重复写；抽够了就直接返回。
            queued = len(pending & unit_ids)
            if queued >= size:
                return []
            candidates = [
                unit for unit in units if unit.id not in decided and unit.id not in pending
            ]
            missing = size - queued
        if not candidates or missing <= 0:
            return []
        chooser = random.Random(job_id)
        picked = sorted(
            chooser.sample(candidates, min(missing, len(candidates))),
            key=lambda unit: unit.ordinal if unit.ordinal is not None else 0,
        )
        rows = [
            {
                "unit_id": unit.id,
                "job_id": job_id,
                "status": "pending",
                "payload": {"ordinal": unit.ordinal, "reason": "batch sample"},
            }
            for unit in picked
        ]
        self.repository.upsert_review_samples(rows, job_id=job_id)
        return rows

    def review_queue(self) -> dict[str, Any]:
        """审阅页数据：待审样本 + 已决样本 + 统计。"""
        samples = self.repository.list_review_samples()
        unit_ids = [row["unit_id"] for row in samples]
        units = {unit_id: self.repository.get_unit(unit_id) for unit_id in unit_ids}
        rows: list[dict[str, Any]] = []
        for sample in samples:
            unit = units.get(sample["unit_id"])
            try:
                payload = json.loads(sample.get("payload") or "{}")
            except ValueError:
                payload = {}
            row = {
                "sample": sample,
                "unit": unit,
                "package": None,
                "ordinal": (unit.ordinal if unit else payload.get("ordinal")),
                "title": None,
                "difficulty": unit.difficulty.value if unit else None,
                "questions": 0,
                "note": payload.get("note", ""),
                "reason": payload.get("reason", ""),
            }
            if unit is not None:
                try:
                    package = self.load_package(unit.id)
                except (FileNotFoundError, ValueError):
                    pass
                else:
                    row["package"] = package
                    row["title"] = package.passage.title
                    row["questions"] = sum(
                        len(group.questions) for group in package.question_groups
                    )
                    row["passed"] = package.quality_report.passed
            rows.append(row)
        pending = [row for row in rows if row["sample"]["status"] == "pending"]
        return {
            "rows": rows,
            "pending": pending,
            "decided": [row for row in rows if row["sample"]["status"] != "pending"],
            "totals": {
                "all": len(rows),
                "pending": len(pending),
                "passed": sum(row["sample"]["status"] == "passed" for row in rows),
                "failed": sum(row["sample"]["status"] == "failed" for row in rows),
            },
        }

    def decide_sample(
        self, unit_id: str, *, approved: bool, note: str = ""
    ) -> dict[str, Any]:
        sample = self.repository.get_review_sample(unit_id)
        if sample is None:
            raise KeyError(f"Unit {unit_id} is not in the review queue")
        status = "passed" if approved else "failed"
        self.repository.decide_review_sample(
            unit_id,
            status=status,
            decision="approve" if approved else "rework",
            note=note,
        )
        return {"unit_id": unit_id, "status": status, "note": note}

    def resume_job(self, job_id: str):
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        if job["status"] == "paused":
            self.repository.update_job(job_id, status="queued", payload=job["payload"])
        self.repository.recover_interrupted_units()
        return self.run_job(job_id)

    def retry_job(self, job_id: str, *, failed_only: bool = True):
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        units = self.repository.list_job_units(job_id)
        if failed_only:
            units = [unit for unit in units if unit.status == UnitStatus.FAILED]
        return self.create_job(job["corpus_id"], [unit.ordinal for unit in units])

    def load_package(self, unit_id: str) -> ReadingPackage:
        path = self.store._destination(Path("packages") / f"{unit_id}.json")
        if not path.is_file():
            raise FileNotFoundError(f"Package not found for unit {unit_id}")
        return ReadingPackage.model_validate_json(path.read_text(encoding="utf-8"))

    def export_job(
        self,
        job_id: str,
        formats: set[str],
        *,
        workbook_size: int = 20,
    ) -> list[Path]:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        packages = [
            self.load_package(unit.id)
            for unit in self.repository.list_job_units(job_id)
            if unit.status == UnitStatus.COMPLETED
        ]
        destination = self.config.output_dir / "exports" / job_id
        paths: list[Path] = []
        for package in packages:
            paths.extend(self.exporter.export_package(package, destination, formats - {"docx"}))
        if "docx" in formats:
            if len(packages) == 1:
                paths.extend(self.exporter.export_package(packages[0], destination, {"docx"}))
            elif packages:
                paths.extend(self.exporter.export_workbooks(packages, destination, workbook_size))
        return paths


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _remove_path(path: Path) -> None:
    """Delete a corpus artifact, refusing anything outside the known roots."""
    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
