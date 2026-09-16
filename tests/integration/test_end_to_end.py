from __future__ import annotations

import socket

import pytest

from app.config import AppConfig
from app.models import Difficulty, UnitStatus
from app.pipeline.service import ReadingStudioService
from app.planning.units import default_question_types
from tests.fixtures.valid_generation import DeterministicProvider


def test_import_sample_approve_batch_export_round_trip(tmp_path, monkeypatch):
    source = tmp_path / "book.txt"
    source.write_text(
        "\n".join(f"第{index}章\n" + "正文。" * 200 for index in range(1, 9)),
        encoding="utf-8",
    )
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    provider = DeterministicProvider()
    studio = ReadingStudioService(config, provider=provider)

    original_socket = socket.socket
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access attempted"))
    corpus = studio.import_source(source).corpus
    sample = studio.generate_sample(
        corpus.id,
        Difficulty.STANDARD,
        default_question_types(Difficulty.STANDARD),
    )
    assert sample.status == UnitStatus.COMPLETED
    studio.approve_sample(corpus.id, sample.unit_id)
    job = studio.create_job(corpus.id, ordinals=[2, 3, 4])
    summary = studio.run_job(job["id"])
    assert len(summary.completed) == 3
    assert all(unit.status == UnitStatus.COMPLETED for unit in studio.repository.list_job_units(job["id"]))

    paths = studio.export_job(job["id"], formats={"json", "html", "docx"})
    assert {path.suffix for path in paths} == {".json", ".html", ".docx"}
    assert len(provider.requests) >= 4
    monkeypatch.setattr(socket, "socket", original_socket)


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_generates_each_supported_difficulty_with_fake_provider(tmp_path, difficulty):
    source = tmp_path / f"{difficulty.value}.txt"
    source.write_text(
        "第一章 开始\n" + "正文。" * 300 + "\n第二章 后续\n" + "后文。" * 300,
        encoding="utf-8",
    )
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    provider = DeterministicProvider(difficulty)
    studio = ReadingStudioService(config, provider=provider)
    corpus = studio.import_source(
        source,
        difficulty=difficulty,
        question_types=default_question_types(difficulty),
    ).corpus

    result = studio.generate_sample(
        corpus.id,
        difficulty,
        default_question_types(difficulty),
    )

    assert result.status == UnitStatus.COMPLETED
    assert result.package.passage.difficulty == difficulty
