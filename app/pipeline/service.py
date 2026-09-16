from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.author import AuthorAgent
from app.agents.deepseek import DeepSeekProvider
from app.agents.examiner import ExaminerAgent
from app.config import AppConfig
from app.models import GenerationUnit
from app.planning.importer import CorpusImporter
from app.planning.units import unit_source_text
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository

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
