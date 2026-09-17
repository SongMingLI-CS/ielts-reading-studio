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

#: Set when a manifest had to be rebuilt from the SQLite index, so the UI can
#: say "confidence not recorded" instead of reporting a misleading 0%.
MANIFEST_REBUILT_DIAGNOSTIC = "manifest_rebuilt_from_index"


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
        existing_path = self.locate_manifest(corpus)
        if existing_path is not None:
            existing = CorpusManifest.model_validate_json(
                existing_path.read_text(encoding="utf-8")
            )
            if (
                existing.corpus.source_path != corpus.source_path
                or existing.corpus.name != corpus.name
            ):
                # The same bytes arrived from a new location: keep the whole index
                # (chapters, units, boundary overrides) and only repair the binding.
                return self._rebind(existing, corpus, existing_path)
            return existing
        repaired = self._repair_manifest(corpus)
        if repaired is not None:
            # The index is authoritative in SQLite, so a lost manifest is rebuilt
            # from it instead of re-planning (which would collide with the units).
            return repaired

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

    def locate_manifest(self, corpus: Corpus) -> Path | None:
        """Manifest for a corpus, tolerating a renamed or moved source file.

        Artifact directories are named ``<source slug>-<corpus id[:8]>``, so the
        trailing id is used as a stable fallback key.
        """
        path = self.manifest_path(corpus)
        if path.is_file():
            return path
        matches = sorted(self.store.root.glob(f"*-{corpus.id[:8]}/manifest.json"))
        return matches[0] if matches else None

    def _rebind(
        self,
        existing: CorpusManifest,
        corpus: Corpus,
        existing_path: Path,
    ) -> CorpusManifest:
        """Point an existing corpus at a new copy of the same source file."""
        old_dir = existing_path.parent
        new_dir = self.manifest_path(corpus).parent
        if old_dir != new_dir and old_dir.is_dir() and not new_dir.exists():
            old_dir.rename(new_dir)
        manifest = existing.model_copy(update={"corpus": corpus})
        self.store.write_json(self.manifest_relative_path(corpus), manifest)
        if self.repository is not None:
            self.repository.add_corpus(corpus)
        return manifest

    def _repair_manifest(self, corpus: Corpus) -> CorpusManifest | None:
        """Rebuild a missing manifest from the SQLite index, without re-planning."""
        if self.repository is None:
            return None
        if self.repository.get_corpus(corpus.id) is None:
            return None
        chapters = self.repository.list_source_chapters(corpus.id)
        units = self.repository.list_units(corpus.id)
        if not chapters and not units:
            return None
        repaired_corpus = corpus.model_copy(update={"chapter_count": len(chapters)})
        manifest = build_manifest(
            repaired_corpus,
            chapters,
            units,
            diagnostics=[MANIFEST_REBUILT_DIAGNOSTIC],
        )
        self.store.write_json(self.manifest_relative_path(repaired_corpus), manifest)
        self.repository.add_corpus(repaired_corpus)
        return manifest

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
