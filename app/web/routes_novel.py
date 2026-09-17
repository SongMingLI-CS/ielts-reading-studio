from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ielts_novel.orchestrator import estimate_dry_run
from ielts_novel.processors.chapter_parser import (
    ChapterDetectionError,
    parse_novel,
    write_detection_report,
)
from starlette.requests import Request

from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter(prefix="/novel")
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")
COMPONENT_ROOT = Path(__file__).parents[2] / "components" / "context-novel"
ALLOWED_EXTENSIONS = {".txt", ".docx", ".epub"}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024


def _paths(service: ReadingStudioService) -> dict[str, Path]:
    input_root = service.config.input_dir / "context-novel"
    output_root = service.config.output_dir / "context-novel"
    return {
        "input": input_root,
        "active": input_root / "active",
        "uploads": input_root / "uploads",
        "output": output_root,
        "report": output_root / "reports" / "chapter_detection.json",
        "source": output_root / "active_source.json",
        "run": output_root / "run_status.json",
        "config": output_root / "runtime-config.yaml",
    }


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _progress_counts(database: Path) -> dict[str, int]:
    counts = {"completed": 0, "failed": 0, "running": 0}
    if not database.exists():
        return counts
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM chapter_progress GROUP BY status"
            ).fetchall()
        counts.update({str(status): int(count) for status, count in rows})
    except sqlite3.Error:
        pass
    return counts


def _catalog_size() -> int:
    catalog = _read_json(COMPONENT_ROOT / "data" / "vocabulary.json", [])
    return len(catalog) if isinstance(catalog, list) else 0


def _page_context(service: ReadingStudioService, **extra: Any) -> dict[str, Any]:
    paths = _paths(service)
    output = paths["output"]
    html_files = sorted((output / "html").glob("*.html")) if output.exists() else []
    volume_files = sorted((output / "volumes").glob("*.docx")) if output.exists() else []
    context = {
        "source": _read_json(paths["source"]),
        "report": _read_json(paths["report"]),
        "run": _read_json(paths["run"]),
        "progress": _progress_counts(output / "state.sqlite3"),
        "catalog_size": _catalog_size(),
        "api_ready": service.config.deepseek_api_key is not None,
        "html_files": [path.relative_to(output).as_posix() for path in html_files[-12:]],
        "volume_files": [path.relative_to(output).as_posix() for path in volume_files[-12:]],
    }
    context.update(extra)
    return context


@router.get("")
def novel_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    return TEMPLATES.TemplateResponse(
        request,
        "novel/index.html",
        _page_context(service),
    )


@router.post("/import")
async def import_novel(
    source: Annotated[UploadFile, File()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    extension = Path(source.filename or "").suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="仅支持 TXT、DOCX 或 EPUB")
    paths = _paths(service)
    paths["uploads"].mkdir(parents=True, exist_ok=True)
    temporary = paths["uploads"] / f".{os.urandom(8).hex()}.upload"
    digest = sha256()
    written = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await source.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件不能超过 250 MB")
                digest.update(chunk)
                handle.write(chunk)
        archive = paths["uploads"] / digest.hexdigest() / f"source{extension}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, archive)
        paths["active"].mkdir(parents=True, exist_ok=True)
        active = paths["active"] / f"source{extension}"
        active_temporary = paths["active"] / f".source{extension}.tmp"
        shutil.copyfile(archive, active_temporary)
        for previous in paths["active"].glob("source.*"):
            previous.unlink()
        os.replace(active_temporary, active)
        try:
            result = parse_novel(active)
        except ChapterDetectionError as exc:
            result = exc.result
        write_detection_report(result, paths["report"])
        _write_json(
            paths["source"],
            {
                "name": Path(source.filename or active.name).name,
                "bytes": written,
                "sha256": digest.hexdigest(),
                "path": str(active),
                "chapters": len(result.chapters),
                "confident": result.confident,
            },
        )
    finally:
        await source.close()
        temporary.unlink(missing_ok=True)
    return RedirectResponse("/novel", status_code=303)


@router.post("/estimate")
def estimate_novel(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    start: Annotated[int, Form(ge=1)] = 1,
    end: Annotated[int, Form(ge=1)] = 1,
):
    source = _read_json(_paths(service)["source"])
    if not source or not source.get("confident"):
        raise HTTPException(status_code=409, detail="请先导入并成功识别小说章节")
    result = parse_novel(source["path"])
    if end < start or end > len(result.chapters):
        raise HTTPException(status_code=422, detail="章节范围无效")
    counts = [
        sum(len(paragraph.text) for paragraph in result.chapters[index - 1].paragraphs)
        for index in range(start, end + 1)
    ]
    estimate = estimate_dry_run(counts, concurrency=6)
    return TEMPLATES.TemplateResponse(
        request,
        "novel/index.html",
        _page_context(service, estimate=estimate, selected_start=start, selected_end=end),
    )


def _run_component(
    config_path: Path,
    arguments: list[str],
    status_path: Path,
    description: str,
) -> None:
    _write_json(status_path, {"status": "running", "description": description})
    log_path = status_path.parent / "reports" / "web-generation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ielts_novel.cli",
                *arguments,
                "--config",
                str(config_path),
            ],
            cwd=COMPONENT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    _write_json(
        status_path,
        {
            "status": "completed" if result.returncode == 0 else "failed",
            "description": description,
            "return_code": result.returncode,
        },
    )


def _write_runtime_config(
    service: ReadingStudioService,
    paths: dict[str, Path],
    *,
    batch_confirmed: bool,
    max_chapters: int,
) -> None:
    runtime_config = {
        "input_dir": str(paths["active"]),
        "output_dir": str(paths["output"]),
        "vocabulary_path": str(COMPONENT_ROOT / "data" / "vocabulary.json"),
        "deepseek_base_url": service.config.deepseek_base_url,
        "deepseek_model": service.config.author_model,
        "deepseek_review_model": service.config.examiner_model,
        "concurrency": min(3, service.config.concurrency),
        "max_chapters_per_run": max_chapters,
        "max_estimated_tokens_per_run": service.config.max_estimated_tokens_per_run,
        "batch_confirmed": batch_confirmed,
    }
    paths["config"].parent.mkdir(parents=True, exist_ok=True)
    paths["config"].write_text(
        yaml.safe_dump(runtime_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _require_generation_ready(
    service: ReadingStudioService,
    paths: dict[str, Path],
) -> dict[str, Any]:
    if service.config.deepseek_api_key is None:
        raise HTTPException(status_code=409, detail="服务器尚未配置 DEEPSEEK_API_KEY")
    source = _read_json(paths["source"])
    if not source or not source.get("confident"):
        raise HTTPException(status_code=409, detail="请先导入并成功识别小说章节")
    run = _read_json(paths["run"], {})
    if run.get("status") == "running":
        raise HTTPException(status_code=409, detail="已有生成任务正在运行")
    return source


@router.post("/generate-sample")
def generate_sample(
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    chapter: Annotated[int, Form(ge=1)] = 1,
    confirmation: Annotated[str, Form()] = "",
):
    if confirmation.strip() != "确认生成样章":
        raise HTTPException(status_code=422, detail="请输入：确认生成样章")
    paths = _paths(service)
    source = _require_generation_ready(service, paths)
    if chapter > int(source["chapters"]):
        raise HTTPException(status_code=422, detail="章节编号超出范围")
    _write_runtime_config(service, paths, batch_confirmed=False, max_chapters=1)
    background.add_task(
        _run_component,
        paths["config"],
        ["--chapter", str(chapter)],
        paths["run"],
        f"第 {chapter} 章样章",
    )
    return RedirectResponse("/novel", status_code=303)


@router.post("/generate-batch")
def generate_batch(
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    start: Annotated[int, Form(ge=1)],
    end: Annotated[int, Form(ge=1)],
    confirmation: Annotated[str, Form()] = "",
):
    if confirmation.strip() != "确认批量生成":
        raise HTTPException(status_code=422, detail="请输入：确认批量生成")
    paths = _paths(service)
    source = _require_generation_ready(service, paths)
    if end < start or end > int(source["chapters"]):
        raise HTTPException(status_code=422, detail="章节范围无效")
    if end - start + 1 > 20:
        raise HTTPException(status_code=422, detail="网页单次最多生成 20 章")
    _write_runtime_config(service, paths, batch_confirmed=True, max_chapters=20)
    background.add_task(
        _run_component,
        paths["config"],
        ["--start", str(start), "--end", str(end)],
        paths["run"],
        f"第 {start}–{end} 章批量任务",
    )
    return RedirectResponse("/novel", status_code=303)


@router.post("/recover/{action}")
def recover_batch(
    action: str,
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    confirmation: Annotated[str, Form()] = "",
):
    if action not in {"resume", "retry-failed"}:
        raise HTTPException(status_code=404, detail="未知恢复操作")
    if confirmation.strip() != "确认继续生成":
        raise HTTPException(status_code=422, detail="请输入：确认继续生成")
    paths = _paths(service)
    _require_generation_ready(service, paths)
    _write_runtime_config(service, paths, batch_confirmed=True, max_chapters=20)
    label = "断点继续" if action == "resume" else "失败章节重试"
    background.add_task(
        _run_component,
        paths["config"],
        [f"--{action}"],
        paths["run"],
        label,
    )
    return RedirectResponse("/novel", status_code=303)


@router.get("/files/{relative_path:path}")
def novel_file(
    relative_path: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    root = _paths(service)["output"].resolve()
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.suffix.casefold() not in {".html", ".docx", ".txt", ".xlsx", ".json"}:
        raise HTTPException(status_code=415, detail="不支持下载此文件")
    return FileResponse(target, filename=target.name)


@router.get("/health")
def novel_health() -> dict[str, Any]:
    return {
        "component": "ielts-context-novel-generator",
        "available": COMPONENT_ROOT.is_dir(),
        "catalog_entries": _catalog_size(),
    }
