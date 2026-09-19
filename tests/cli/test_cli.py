from __future__ import annotations

import re

from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

from app.cli import app, parse_range
from app.config import AppConfig
from app.pipeline.service import ReadingStudioService

runner = CliRunner()


def make_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "output_dir: output\ndatabase_path: output/state.db\n",
        encoding="utf-8",
    )
    return path


def make_source(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text(
        "第一章 开始\n" + "正文。" * 300 + "\n第二章 后续\n" + "后文。" * 300,
        encoding="utf-8",
    )
    return path


def import_corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = make_config(tmp_path)
    result = runner.invoke(app, ["import", str(make_source(tmp_path)), "--config", str(config)])
    assert result.exit_code == 0, result.output
    corpus_id = re.search(r"Corpus ID: ([^\s]+)", result.output).group(1)
    return config, corpus_id


def test_import_and_estimate_do_not_require_key(tmp_path, monkeypatch):
    config, corpus_id = import_corpus(tmp_path, monkeypatch)
    estimated = runner.invoke(
        app,
        ["estimate", corpus_id, "--range", "1-1", "--level", "standard", "--config", str(config)],
    )
    assert estimated.exit_code == 0, estimated.output
    assert "预计 API 请求" in estimated.output
    assert "预计 Token" in estimated.output
    assert "金额" in estimated.output


def test_inspect_reports_unapproved_corpus(tmp_path, monkeypatch):
    config, corpus_id = import_corpus(tmp_path, monkeypatch)
    result = runner.invoke(app, ["inspect", corpus_id, "--config", str(config)])
    assert result.exit_code == 0
    assert "Sample approved: no" in result.output


def test_generate_all_requires_literal_confirmation(tmp_path, monkeypatch):
    config, corpus_id = import_corpus(tmp_path, monkeypatch)
    service = ReadingStudioService(AppConfig.load(config))
    service.repository.record_corpus_approval("approval-1", corpus_id, "approved", {"unit_id": "sample"})

    result = runner.invoke(
        app,
        ["generate", corpus_id, "--all", "--config", str(config)],
        input="no\n",
    )

    assert result.exit_code == 2
    assert "未开始生成" in result.output


def test_approve_sample_rejects_incomplete_unit(tmp_path, monkeypatch):
    config, corpus_id = import_corpus(tmp_path, monkeypatch)
    service = ReadingStudioService(AppConfig.load(config))
    unit_id = service.repository.list_units(corpus_id)[0].id
    result = runner.invoke(
        app,
        ["approve-sample", corpus_id, unit_id, "--config", str(config)],
    )
    assert result.exit_code == 2
    assert "completed" in result.output


def test_range_parser_accepts_ranges_and_lists():
    assert parse_range("1-3,5,3") == [1, 2, 3, 5]
    assert parse_range("all") is None


def test_help_lists_complete_command_surface():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "import",
        "inspect",
        "estimate",
        "sample",
        "approve-sample",
        "generate",
        "resume",
        "retry",
        "export",
        "serve",
    ):
        assert command in result.output


def test_remote_serve_requires_web_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("IELTS_WEB_USERNAME", raising=False)
    monkeypatch.delenv("IELTS_WEB_PASSWORD", raising=False)

    result = runner.invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--config", str(make_config(tmp_path))],
    )

    assert result.exit_code == 2
    assert "IELTS_WEB_USERNAME" in result.output


def test_remote_serve_runs_when_web_credentials_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("IELTS_WEB_USERNAME", "reader")
    monkeypatch.setenv("IELTS_WEB_PASSWORD", "secret")
    called = {}
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: called.update(kwargs))

    result = runner.invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--port", "8766", "--config", str(make_config(tmp_path))],
    )

    assert result.exit_code == 0, result.output
    assert called == {"host": "0.0.0.0", "port": 8766, "proxy_headers": True}
    assert "HTTPS" in result.output


def test_local_serve_does_not_warn_about_https(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: called.update(kwargs))

    result = runner.invoke(
        app,
        ["serve", "--port", "8767", "--config", str(make_config(tmp_path))],
    )

    assert result.exit_code == 0, result.output
    assert called == {"host": "127.0.0.1", "port": 8767, "proxy_headers": True}
    assert "HTTPS" not in result.output


def test_migrate_command_creates_and_reports_the_schema(tmp_path):
    config = make_config(tmp_path)

    result = runner.invoke(app, ["migrate", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "未迁移" in result.output
    assert (tmp_path / "output" / "state.db").exists()

    again = runner.invoke(app, ["migrate", "--config", str(config)])

    assert again.exit_code == 0, again.output
    assert "无需迁移。" in again.output


def test_migrate_check_reports_pending_migration_without_touching_the_database(tmp_path):
    config = make_config(tmp_path)

    result = runner.invoke(app, ["migrate", "--check", "--config", str(config)])

    assert result.exit_code == 2, result.output
    assert "数据库需要迁移" in result.output

    # --check is read-only: SQLite creates the file, but no table may be written.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'output' / 'state.db'}")
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_migrate_check_passes_once_the_schema_is_current(tmp_path):
    config = make_config(tmp_path)
    runner.invoke(app, ["migrate", "--config", str(config)])

    result = runner.invoke(app, ["migrate", "--check", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "无需迁移。" in result.output
