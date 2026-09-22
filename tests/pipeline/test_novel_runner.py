"""Context-novel jobs go through the same durable queue as reading jobs."""

from __future__ import annotations

import json
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


def test_exit_code_zero_without_chapters_is_recorded_not_hidden(
    tmp_path: Path, monkeypatch
) -> None:
    """进程退出码 0 但一章都没生成时，状态必须写明原因，不能让页面显示“成功”。"""

    status_path = tmp_path / "run_status.json"
    log_path = tmp_path / "reports" / "web-generation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "总章节数 1 | 已完成 0\n"
        "ERROR: 分块合并后的章节质量检查失败：density_too_low\n"
        '{"requested": 1, "completed": [], "failed": {}, "inserted_total": 0}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.pipeline.novel_runner.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )

    assert (
        run_novel_job(
            None,
            {
                "arguments": ["--chapter", "1"],
                "config_path": str(tmp_path / "c.yaml"),
                "status_path": str(status_path),
                "description": "第 1 章样章",
            },
        )
        == 0
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"  # 进程本身没崩
    assert payload["outcome"] == "no_chapters"  # 但没有任何章节产出
    assert payload["chapters_completed"] == 0
    assert "density_too_low" in payload["failure_reason"]
    assert "每 500 字至少 20 个词条" in payload["failure_hint"]


def test_billing_failures_explain_the_balance_and_the_retry_path(
    tmp_path: Path, monkeypatch
) -> None:
    """余额不足是最常见的一类失败：提示必须说清怎么修、以及重试只跑失败章节。"""

    status_path = tmp_path / "run_status.json"
    log_path = tmp_path / "reports" / "web-generation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "第 6 章失败：billing（连续失败 1）\n第 7 章失败：billing（连续失败 2）\n"
        "连续 5 章失败，已停止批处理\n"
        '{"requested": 20, "completed": [1, 2, 4, 5], "failed": {"6": "billing", "7": "billing"},'
        ' "inserted_total": 745}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.pipeline.novel_runner.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
    )

    try:
        run_novel_job(
            None,
            {
                "arguments": ["--chapters", "1-20"],
                "config_path": str(tmp_path / "c.yaml"),
                "status_path": str(status_path),
            },
        )
    except RuntimeError:
        pass  # 非零退出码照旧抛给 worker

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["chapters_failed"] == 2
    assert "billing" in payload["failure_reason"]
    assert "余额或配额不足" in payload["failure_hint"]
    assert "重试失败章节" in payload["failure_hint"]


def test_latest_run_in_the_append_only_log_decides_the_outcome(
    tmp_path: Path, monkeypatch
) -> None:
    """日志是追加写的：这次的结果要从自己新写的那几行里读，不能沿用上一轮的失败。"""

    status_path = tmp_path / "run_status.json"
    log_path = tmp_path / "reports" / "web-generation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "ERROR: 分块合并后的章节质量检查失败：density_too_low\n"  # 上一轮留下的
        '{"requested": 2, "completed": [1, 2], "failed": {}, "inserted_total": 40}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.pipeline.novel_runner.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )

    run_novel_job(
        None,
        {
            "arguments": ["--chapters", "1-2"],
            "config_path": str(tmp_path / "c.yaml"),
            "status_path": str(status_path),
        },
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "ok"
    assert payload["chapters_completed"] == 2
    assert payload["inserted_total"] == 40
