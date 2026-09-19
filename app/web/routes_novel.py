from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
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

from app.pipeline.queue import NOVEL_KIND
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


def _chapter_outputs(
    output: Path,
    page: int,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    entries = _read_json(output / "index_entries.json", [])
    titles = {
        int(item[0]): str(item[1])
        for item in entries
        if isinstance(item, list) and len(item) == 2
    }
    chapters = []
    for chapter_id, title in sorted(titles.items()):
        html_path = output / "html" / f"第{chapter_id:04d}章.html"
        if not html_path.is_file():
            continue
        chapters.append(
            {
                "id": chapter_id,
                "title": title,
                "has_docx": (
                    output / "chapters" / f"第{chapter_id:04d}章.docx"
                ).is_file(),
            }
        )
    total_pages = max(1, (len(chapters) + page_size - 1) // page_size)
    selected_page = min(page, total_pages)
    start = (selected_page - 1) * page_size
    return chapters[start : start + page_size], total_pages


def _page_context(
    service: ReadingStudioService,
    *,
    page: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    paths = _paths(service)
    output = paths["output"]
    volume_files = sorted((output / "volumes").glob("*.docx")) if output.exists() else []
    chapter_outputs, total_pages = _chapter_outputs(output, page)
    library_files = []
    if output.exists():
        library_files = [
            path
            for path in [output / "index.html", output / "glossary.xlsx", *sorted(output.glob("*.txt"))]
            if path.is_file()
        ]
    context = {
        "source": _read_json(paths["source"]),
        "report": _read_json(paths["report"]),
        "run": _read_json(paths["run"]),
        "progress": _progress_counts(output / "state.sqlite3"),
        "catalog_size": _catalog_size(),
        "api_ready": service.config.deepseek_api_key is not None,
        "chapter_outputs": chapter_outputs,
        "page": min(page, total_pages),
        "total_pages": total_pages,
        "volume_files": [path.relative_to(output).as_posix() for path in volume_files[-12:]],
        "library_files": [path.relative_to(output).as_posix() for path in library_files],
    }
    context.update(extra)
    return context


@router.get("")
def novel_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
):
    return TEMPLATES.TemplateResponse(
        request,
        "novel/index.html",
        _page_context(service, page=page),
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
    if run.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="已有生成任务正在排队或运行")
    return source


def _queue_novel_batch(
    service: ReadingStudioService,
    paths: dict[str, Path],
    *,
    arguments: list[str],
    description: str,
    idempotency_key: str,
) -> None:
    """Hand the component CLI run to the durable worker instead of a request task."""

    from app.pipeline.novel_runner import ensure_system_corpus

    service.queue.enqueue(
        corpus_id=ensure_system_corpus(service),
        kind=NOVEL_KIND,
        payload={
            "arguments": arguments,
            "config_path": str(paths["config"]),
            "status_path": str(paths["run"]),
            "description": description,
        },
        idempotency_key=idempotency_key,
    )


@router.post("/generate-sample")
def generate_sample(
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
    description = f"第 {chapter} 章样章"
    _write_json(paths["run"], {"status": "queued", "description": description})
    _queue_novel_batch(
        service,
        paths,
        arguments=["--chapter", str(chapter)],
        description=description,
        idempotency_key=f"novel-sample:{chapter}",
    )
    return RedirectResponse("/novel", status_code=303)


@router.post("/generate-batch")
def generate_batch(
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
    description = f"第 {start}–{end} 章批量任务"
    _write_json(paths["run"], {"status": "queued", "description": description})
    _queue_novel_batch(
        service,
        paths,
        arguments=["--start", str(start), "--end", str(end)],
        description=description,
        idempotency_key=f"novel-batch:{start}-{end}",
    )
    return RedirectResponse("/novel", status_code=303)


@router.post("/recover/{action}")
def recover_batch(
    action: str,
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
    _write_json(paths["run"], {"status": "queued", "description": label})
    _queue_novel_batch(
        service,
        paths,
        arguments=[f"--{action}"],
        description=label,
        idempotency_key=f"novel-recover:{action}",
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
    if target.suffix.casefold() == ".html":
        return FileResponse(target, media_type="text/html")
    return FileResponse(target, filename=target.name)


def _chapter_title(output: Path, chapter_id: int) -> str:
    entries = _read_json(output / "index_entries.json", [])
    for item in entries:
        if isinstance(item, list) and len(item) == 2 and int(item[0]) == chapter_id:
            return str(item[1])
    return f"第{chapter_id:04d}章"


@router.get("/preview/{chapter_id}")
def preview_chapter(
    chapter_id: int,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    target = _paths(service)["output"] / "html" / f"第{chapter_id:04d}章.html"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="章节尚未生成")
    return FileResponse(target, media_type="text/html")


@router.get("/download/{chapter_id}/{format_name}")
def download_chapter(
    chapter_id: int,
    format_name: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    formats = {
        "html": ("html", ".html"),
        "docx": ("chapters", ".docx"),
        "json": ("chapter_json", ".json"),
    }
    if format_name not in formats:
        raise HTTPException(status_code=404, detail="未知文件格式")
    directory, suffix = formats[format_name]
    output = _paths(service)["output"]
    target = output / directory / f"第{chapter_id:04d}章{suffix}"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="章节文件尚未生成")
    title = re.sub(
        r'[\\/:*?"<>|\x00-\x1f]',
        "_",
        _chapter_title(output, chapter_id),
    ).strip()
    filename = f"{chapter_id:04d}_{title}{suffix}"
    return FileResponse(
        target,
        filename=filename,
        content_disposition_type="attachment",
    )


@router.get("/health")
def novel_health() -> dict[str, Any]:
    return {
        "component": "ielts-context-novel-generator",
        "available": COMPONENT_ROOT.is_dir(),
        "catalog_entries": _catalog_size(),
    }
