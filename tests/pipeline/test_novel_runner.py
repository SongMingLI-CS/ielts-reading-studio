"""Context-novel jobs go through the same durable queue as reading jobs."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import AppConfig
from app.pipeline.novel_runner import ensure_system_corpus, run_novel_job
from app.pipeline.queue import NOVEL_KIND
from app.pipeline.service import ReadingStudioService
from app.pipeline.worker import JobWorker
from app.storage.repositories import SYSTEM_CORPUS_ID


def make_studio(tmp_path: Path) -> ReadingStudioService:
    config = AppConfig(
        base_dir=tmp_path,
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )
    return ReadingStudioService(config)


def test_system_corpus_is_created_once_and_hidden_from_corpus_lists(tmp_path: Path) -> None:
    studio = make_studio(tmp_path)

    first = ensure_system_corpus(studio)
    second = ensure_system_corpus(studio)

    assert first == second == SYSTEM_CORPUS_ID
    assert studio.repository.get_corpus(SYSTEM_CORPUS_ID) is not None
    assert studio.repository.list_corpora() == []


def test_worker_runs_a_novel_job_through_the_component_cli(
    tmp_path: Path, monkeypatch
) -> None:
    studio = make_studio(tmp_path)
    status_path = tmp_path / "output" / "context-novel" / "run_status.json"
    config_path = tmp_path / "output" / "context-novel" / "runtime-config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("output_dir: output\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # mirrors subprocess.run for this test
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("app.pipeline.novel_runner.subprocess.run", fake_run)
    studio.queue.enqueue(
        corpus_id=ensure_system_corpus(studio),
        kind=NOVEL_KIND,
        payload={
            "arguments": ["--chapter", "1"],
            "config_path": str(config_path),
            "status_path": str(status_path),
            "description": "第 1 章样章",
        },
    )

    finished = JobWorker(studio).run_once()

    assert finished.kind == NOVEL_KIND
    assert finished.status == "completed"
    assert "-m" in calls[0] and "ielts_novel.cli" in calls[0]
    assert "completed" in status_path.read_text(encoding="utf-8")


def test_novel_runner_marks_failures_and_propagates_them(tmp_path: Path, monkeypatch) -> None:
    status_path = tmp_path / "run_status.json"
    monkeypatch.setattr(
        "app.pipeline.novel_runner.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 2),
    )

    try:
        run_novel_job(
            None,
            {"arguments": [], "config_path": str(tmp_path / "c.yaml"), "status_path": str(status_path)},
        )
    except RuntimeError as exc:
        assert "code 2" in str(exc)
    else:  # pragma: no cover - the runner must not swallow a component failure
        raise AssertionError("run_novel_job should raise when the component fails")

    payload = status_path.read_text(encoding="utf-8")
    assert '"failed"' in payload
    assert '"return_code": 2' in payload.replace("\n", " ").replace("  ", " ")
