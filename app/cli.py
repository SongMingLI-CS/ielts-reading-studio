from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from app.config import AppConfig, ConfigurationError, redact_secrets
from app.models import Difficulty
from app.pipeline.service import ReadingStudioService
from app.planning.units import default_question_types
from app.storage.migrations import MigrationError, schema_status_for_path
from app.storage.snapshot import snapshot_database

app = typer.Typer(
    no_args_is_help=True,
    help="Local IELTS Academic Reading generation and practice studio.",
)


def build_service(config_path: Path, *, require_api_key: bool = False) -> ReadingStudioService:
    return ReadingStudioService(AppConfig.load(config_path, require_api_key=require_api_key))


@app.command("import")
def import_source(
    source: Annotated[Path, typer.Argument(exists=True, readable=True)],
    level: Annotated[Difficulty, typer.Option("--level")] = Difficulty.STANDARD,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Import and index a source without making any API request."""
    service = _service(config)
    manifest = _guard(
        lambda: service.import_source(
            source,
            difficulty=level,
            question_types=default_question_types(level),
        )
    )
    typer.echo(f"Corpus ID: {manifest.corpus.id}")
    typer.echo(f"章节: {manifest.chapter_count}; 生成单元: {manifest.unit_count}")
    typer.echo(f"解析置信度: {manifest.confidence:.3f}")
    if manifest.diagnostics:
        typer.echo("诊断: " + ", ".join(manifest.diagnostics))


@app.command()
def inspect(
    corpus_id: str,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Inspect corpus metadata, status counts, and sample approval."""
    details = _guard(lambda: _service(config).inspect_corpus(corpus_id))
    corpus = details["corpus"]
    typer.echo(f"Corpus ID: {corpus.id}")
    typer.echo(f"Name: {corpus.name}")
    typer.echo(f"Chapters: {corpus.chapter_count}; Units: {details['unit_count']}")
    typer.echo("Statuses: " + json.dumps(details["statuses"], ensure_ascii=False, sort_keys=True))
    typer.echo(f"Sample approved: {'yes' if details['approval'] else 'no'}")


@app.command()
def estimate(
    corpus_id: str,
    range_spec: Annotated[str | None, typer.Option("--range")] = None,
    level: Annotated[Difficulty, typer.Option("--level")] = Difficulty.STANDARD,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Estimate requests and tokens entirely offline."""
    service = _service(config)
    ordinals = _guard(lambda: parse_range(range_spec))
    value = _guard(lambda: service.estimate_corpus(corpus_id, ordinals))
    typer.echo(f"目标难度: {level.value}")
    _print_estimate(value)


@app.command()
def sample(
    corpus_id: str,
    level: Annotated[Difficulty, typer.Option("--level")] = Difficulty.STANDARD,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Generate exactly one sample; never approves it automatically."""
    service = _service(config, require_api_key=True)
    result = _guard(
        lambda: service.generate_sample(corpus_id, level, default_question_types(level))
    )
    typer.echo(f"Sample unit: {result.unit_id}")
    typer.echo(f"Status: {result.status.value}")
    typer.echo("Review this sample before running approve-sample.")


@app.command("approve-sample")
def approve_sample(
    corpus_id: str,
    unit_id: str,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Record explicit human approval of a completed sample."""
    approval = _guard(lambda: _service(config).approve_sample(corpus_id, unit_id))
    typer.echo(f"Approved sample: {approval['payload']['unit_id']}")


@app.command()
def generate(
    corpus_id: str,
    range_spec: Annotated[str | None, typer.Option("--range")] = None,
    all_units: Annotated[bool, typer.Option("--all")] = False,
    level: Annotated[Difficulty, typer.Option("--level")] = Difficulty.STANDARD,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Create and synchronously run an approved batch."""
    if all_units and range_spec:
        _abort("--all and --range cannot be used together")
    service = _service(config)
    if not service.is_corpus_approved(corpus_id):
        _abort("样篇尚未批准；请先运行 approve-sample")
    ordinals = None if all_units else _guard(lambda: parse_range(range_spec))
    value = _guard(lambda: service.estimate_corpus(corpus_id, ordinals))
    _print_estimate(value)
    if all_units and not yes:
        confirmation = typer.prompt("输入“确认全部生成”以继续", default="")
        if confirmation != "确认全部生成":
            _abort("未开始生成")
    if service.config.deepseek_api_key is None:
        _abort("DEEPSEEK_API_KEY is required for API work")
    job = _guard(
        lambda: service.create_job(
            corpus_id,
            ordinals,
            difficulty=level,
            question_types=default_question_types(level),
        )
    )
    typer.echo(f"Job ID: {job['id']}")
    summary = _guard(lambda: service.run_job(job["id"]))
    typer.echo(
        f"Completed: {len(summary.completed)}; Failed: {len(summary.failed)}; "
        f"Needs review: {len(summary.needs_review)}"
    )


@app.command()
def resume(
    job_id: str,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Recover interrupted units and continue a job."""
    service = _service(config, require_api_key=True)
    summary = _guard(lambda: service.resume_job(job_id))
    typer.echo(f"Resumed {job_id}; completed this run: {len(summary.completed)}")


@app.command()
def retry(
    job_id: str,
    failed_only: Annotated[bool, typer.Option("--failed-only")] = False,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Create a retry job, optionally containing failed units only."""
    service = _service(config, require_api_key=True)
    job = _guard(lambda: service.retry_job(job_id, failed_only=failed_only))
    typer.echo(f"Retry Job ID: {job['id']}")
    summary = _guard(lambda: service.run_job(job["id"]))
    typer.echo(f"Completed this retry: {len(summary.completed)}")


@app.command("export")
def export_command(
    job_id: str,
    format_name: Annotated[str, typer.Option("--format")] = "json",
    workbook_size: Annotated[int, typer.Option("--workbook-size", min=20, max=50)] = 20,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Export completed packages without making an API request."""
    formats = {part.strip().lower() for part in format_name.split(",") if part.strip()}
    if not formats or not formats <= {"json", "html", "docx"}:
        _abort("--format must contain json, html, or docx")
    paths = _guard(lambda: _service(config).export_job(job_id, formats, workbook_size=workbook_size))
    for path in paths:
        typer.echo(str(path))


@app.command()
def snapshot(
    target: Annotated[Path, typer.Option("--target", help="快照输出文件路径")],
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Write a consistent SQLite snapshot; safe to run while the service is running."""
    settings = _guard(lambda: AppConfig.load(config))
    created = _guard(lambda: snapshot_database(settings.database_path, target))
    if not created:
        typer.echo(f"数据库尚不存在，未生成快照: {settings.database_path}")
        return
    typer.echo(f"快照: {target}")


@app.command()
def migrate(
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
    check: Annotated[bool, typer.Option("--check", help="只显示版本状态，不修改数据库")] = False,
) -> None:
    """Apply pending SQLite schema migrations, or report the current revision."""
    from app.storage.database import Database

    settings = _guard(lambda: AppConfig.load(config))
    database = Database(settings.database_path)
    current, head = _guard(lambda: schema_status_for_path(settings.database_path))
    typer.echo(f"数据库: {settings.database_path}")
    typer.echo(f"当前版本: {current or '未迁移'}")
    typer.echo(f"目标版本: {head}")
    if current == head:
        typer.echo("无需迁移。")
        return
    if check:
        _abort(f"数据库需要迁移: {current or '未迁移'} -> {head}")
    result = _guard(lambda: database.migrate())
    typer.echo(f"已迁移: {result.from_revision or '未迁移'} -> {result.to_revision}")


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8000,
    config: Annotated[Path, typer.Option("--config")] = Path("config.yaml"),
) -> None:
    """Start the FastAPI interface."""
    import uvicorn

    from app.web.app import create_app

    service = _service(config)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    remote = host not in local_hosts
    if remote and not (service.config.web_username and service.config.web_password):
        _abort("Remote serving requires IELTS_WEB_USERNAME and IELTS_WEB_PASSWORD")
    typer.echo(f"http://{host}:{port}")
    if remote:
        typer.echo(
            "提示：公网访问请放在 HTTPS 反向代理之后。浏览器只在安全上下文"
            "（https 或 localhost）提供部分 Web API，且 Basic 口令不能在明文 HTTP 上传输。"
            "反代需转发 Host、X-Forwarded-For 与 X-Forwarded-Proto。"
        )
    uvicorn.run(
        create_app(config=service.config, service=service),
        host=host,
        port=port,
        proxy_headers=True,
    )


def parse_range(value: str | None) -> list[int] | None:
    if value is None or not value.strip() or value.strip().casefold() == "all":
        return None
    result: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise ValueError("Range contains an empty item")
        if "-" in token:
            pieces = token.split("-", 1)
            start, end = (int(piece) for piece in pieces)
            if start < 1 or end < start:
                raise ValueError(f"Invalid range: {token}")
            result.extend(range(start, end + 1))
        else:
            ordinal = int(token)
            if ordinal < 1:
                raise ValueError("Unit ordinals start at 1")
            result.append(ordinal)
    return list(dict.fromkeys(result))


def _service(config: Path, *, require_api_key: bool = False) -> ReadingStudioService:
    return _guard(lambda: build_service(config, require_api_key=require_api_key))


def _print_estimate(value) -> None:
    typer.echo(f"生成单元: {value.unit_count}")
    typer.echo(f"预计 API 请求: {value.minimum_requests}–{value.maximum_requests}")
    typer.echo(f"预计 Token: {value.minimum_tokens}–{value.maximum_tokens}")
    if not value.pricing_available:
        typer.echo("未配置价格，不显示金额估算。")


def _guard(action):
    try:
        return action()
    except (
        ConfigurationError,
        MigrationError,
        KeyError,
        ValueError,
        PermissionError,
        FileNotFoundError,
    ) as exc:
        _abort(redact_secrets(exc))


def _abort(message: object) -> None:
    typer.echo(str(message), err=True)
    raise typer.Exit(code=2)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
