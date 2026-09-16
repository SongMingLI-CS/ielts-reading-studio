from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid5

from app.config import AppConfig
from app.models import (
    Corpus,
    CorpusManifest,
    Difficulty,
    GenerationUnit,
    QuestionType,
    SourceChapter,
)
from app.parsers import PARSER_VERSION, parse_source
from app.planning.units import build_manifest, default_question_types, plan_units
from app.storage.artifacts import ArtifactStore
from app.storage.repositories import Repository

CORPUS_NAMESPACE = UUID("1f4a7c39-2b58-4d61-8e0a-77c5b3d9e210")

SLUG_PATTERN = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

MANIFEST_FILENAME = "manifest.json"


def corpus_id_for(source_hash: str) -> str:
    """Deterministic corpus identity so re-importing one file reuses its state."""
    return str(uuid5(CORPUS_NAMESPACE, source_hash))


def corpus_directory(corpus: Corpus) -> str:
    """Human-readable output directory name for one corpus."""
    stem = SLUG_PATTERN.sub("-", Path(corpus.source_path).stem).strip("-").lower()
    return f"{stem or 'corpus'}-{corpus.id[:8]}"


class CorpusImporter:
    """Offline import path: parse, plan and index a corpus without any API client."""

    def __init__(
        self,
        config: AppConfig,
        *,
        repository: Repository | None = None,
        store: ArtifactStore | None = None,
    ):
        self.config = config
        self.repository = repository
        self.store = store if store is not None else ArtifactStore(config.output_dir)

    def import_source(
        self,
        path: str | Path,
        *,
        difficulty: Difficulty = Difficulty.STANDARD,
        question_types: list[QuestionType] | None = None,
    ) -> CorpusManifest:
        """Read one source file, index its chapters and plan its generation units."""
        source_path = Path(path).expanduser()
        result = parse_source(source_path)
        corpus = Corpus(
            id=corpus_id_for(result.source_hash),
            name=source_path.name,
            source_path=str(source_path),
            source_hash=result.source_hash,
            format=result.format,
            encoding=result.encoding,
            chapter_count=len(result.chapters),
            parser_version=PARSER_VERSION,
        )
        existing = self.load_manifest(corpus)
        if existing is not None:
            return existing

        types = list(question_types) if question_types else default_question_types(difficulty)
        units = plan_units(result.chapters, self.config, difficulty, types, corpus_id=corpus.id)
        manifest = build_manifest(
            corpus,
            result.chapters,
            units,
            confidence=result.confidence,
            diagnostics=result.diagnostics,
            candidate_chapters=result.candidate_chapters,
        )
        self._persist(corpus, result.chapters, units)
        self.store.write_json(self.manifest_relative_path(corpus), manifest)
        return manifest

    def manifest_path(self, corpus: Corpus) -> Path:
        return self.store.root / self.manifest_relative_path(corpus)

    def manifest_relative_path(self, corpus: Corpus) -> Path:
        return Path(corpus_directory(corpus)) / MANIFEST_FILENAME

    def load_manifest(self, corpus: Corpus) -> CorpusManifest | None:
        """Return the stored manifest when this corpus was already imported."""
        if self.repository is not None and self.repository.get_corpus(corpus.id) is None:
            return None
        path = self.manifest_path(corpus)
        if not path.is_file():
            return None
        return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _persist(
        self,
        corpus: Corpus,
        chapters: list[SourceChapter],
        units: list[GenerationUnit],
    ) -> None:
        if self.repository is None:
            return
        self.repository.add_corpus(corpus)
        self.repository.add_source_chapters(corpus.id, chapters)
        self.repository.add_units(units)
