from __future__ import annotations

import socket
import sys

import pytest

from app.config import AppConfig
from app.planning.importer import CorpusImporter
from app.storage.artifacts import ArtifactStore
from app.storage.database import Database
from app.storage.repositories import Repository


@pytest.fixture
def service(tmp_path):
    # Keep every data path explicit: with the bare relative defaults this import
    # fixture wrote its corpora into the repository's own output/ directory,
    # which on a deployment is the live data directory.
    config = AppConfig(
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    database = Database(config.database_path)
    database.create_schema()
    return CorpusImporter(
        config,
        repository=Repository(database),
        store=ArtifactStore(config.output_dir),
    )


def test_two_thousand_chapter_manifest_is_stable_and_offline(tmp_path, monkeypatch, service):
    source = tmp_path / "huge.txt"
    source.write_text(
        "\n".join(f"第{i}章\n" + "正文。" * 200 for i in range(1, 2001)),
        encoding="utf-8",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("network access attempted during import")

    agent_modules_before = {name for name in sys.modules if name.startswith("app.agents")}
    monkeypatch.setattr(socket, "socket", forbidden)
    manifest = service.import_source(source)

    assert manifest.chapter_count == 2000
    assert manifest.unit_count > 0
    assert manifest.corpus.source_hash
    assert {name for name in sys.modules if name.startswith("app.agents")} == agent_modules_before
    assert manifest.units[0].ordinal == 1


def test_manifest_json_is_written_atomically_and_reused_on_reimport(tmp_path, service):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n" + "正文。" * 300 + "\n第二章 后续\n" + "后文。" * 300, encoding="utf-8")

    first = service.import_source(source)
    second = service.import_source(source)

    manifest_path = service.manifest_path(first.corpus)
    assert manifest_path.is_file()
    assert list(manifest_path.parent.glob("*.tmp")) == []
    assert [unit.id for unit in second.units] == [unit.id for unit in first.units]
    assert second.unit_count == first.unit_count


def test_unreliable_boundaries_import_without_units(tmp_path, service):
    source = tmp_path / "broken.txt"
    source.write_text("没有章节边界的正文。" * 1000, encoding="utf-8")

    manifest = service.import_source(source)

    assert manifest.chapter_count == 0
    assert manifest.unit_count == 0
    assert "no_reliable_boundaries" in manifest.diagnostics
