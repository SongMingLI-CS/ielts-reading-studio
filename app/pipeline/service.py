from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agents.author import AuthorAgent
from app.agents.deepseek import DeepSeekProvider
from app.agents.examiner import ExaminerAgent
from app.config import AppConfig
from app.exporters.service import ExportService
from app.models import (
    Difficulty,
    GenerationUnit,
    QuestionType,
    ReadingPackage,
    UnitStatus,
)
from app.planning.estimate import estimate_run
from app.planning.importer import CorpusImporter
from app.planning.units import default_question_types, unit_source_text
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository

from .batch_runner import BatchRunner
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
        self.database.create_schema()
        self.repository = Repository(self.database)
        self.store = ArtifactStore(config.output_dir)
        self.importer = CorpusImporter(config, repository=self.repository, store=self.store)
        self.exporter = ExportService()
        self._provider = provider

    def import_source(self, path: str | Path):
        return self.importer.import_source(Path(path))

    def source_text_for_unit(self, unit: GenerationUnit) -> str:
        chapters = {
            chapter_id: self.repository.get_source_chapter(chapter_id)
            for chapter_id in unit.source_chapter_ids
        }
        if any(chapter is None for chapter in chapters.values()):
            raise KeyError(f"Source chapters missing for unit {unit.id}")
        return unit_source_text(unit, chapters)  # type: ignore[arg-type]

    def unit_runner(self) -> UnitRunner:
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

    def generate_sample(
        self,
        corpus_id: str,
        difficulty: Difficulty = Difficulty.STANDARD,
        question_types: list[QuestionType] | None = None,
    ):
        units = self.selected_units(corpus_id)
        if not units:
            raise ValueError("Corpus has no reliable generation units")
        unit = units[0]
        selected_types = question_types or default_question_types(difficulty)
        if unit.difficulty != difficulty or unit.question_types != selected_types:
            raise ValueError(
                "The requested sample settings differ from the immutable corpus manifest; re-plan the corpus"
            )
        return self.unit_runner().run(unit.id)

    def approve_sample(self, corpus_id: str, unit_id: str) -> dict[str, Any]:
        unit = self.repository.get_unit(unit_id)
        if unit is None or unit.corpus_id != corpus_id:
            raise ValueError("Sample unit does not belong to the corpus")
        if unit.status != UnitStatus.COMPLETED:
            raise ValueError("Sample unit must be completed before approval")
        approval_id = f"{corpus_id}:{unit_id}"
        payload = {"unit_id": unit_id, "approved": True}
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
    ) -> dict[str, Any]:
        if not self.is_corpus_approved(corpus_id):
            raise PermissionError("A completed sample must be explicitly approved before batch generation")
        units = [unit for unit in self.selected_units(corpus_id, ordinals) if unit.status != UnitStatus.COMPLETED]
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
        }
        self.repository.create_job(job_id, corpus_id, "queued", payload)
        self.repository.assign_units_to_job(payload["unit_ids"], job_id)
        return self.repository.get_job(job_id)

    def run_job(self, job_id: str):
        return BatchRunner(
            self.config,
            self.repository,
            self.store,
            self.unit_runner(),
        ).run(job_id)

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
