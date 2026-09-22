from __future__ import annotations

import os
import re
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
from ielts_novel.orchestrator import estimate_dry_run
from ielts_novel.processors.chapter_parser import (
    ChapterDetectionError,
    parse_novel,
    write_detection_report,
)
from starlette.requests import Request

from app.pipeline.queue import NOVEL_KIND
from app.pipeline.service import ReadingStudioService
from app.security.uploads import check_magic, validate_extension

from .dependencies import get_service
from .novel_library import (
    book_entry,
    book_paths,
    input_root,
    list_books,
    migrate_legacy,
    output_root,
    progress_counts,
    read_index,
    read_json,
    register_book,
    resolve_book,
    set_active_book,
    write_json,
)
from .templating import templates

router = APIRouter(prefix="/novel")
TEMPLATES = templates()
COMPONENT_ROOT = Path(__file__).parents[2] / "components" / "context-novel"
ALLOWED_EXTENSIONS = {".txt", ".docx", ".epub"}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024

#: 旧名字保留，改动集中在书库模块里。
_read_json = read_json
_write_json = write_json


def _paths(service: ReadingStudioService, book: dict[str, Any]) -> dict[str, Any]:
    """一本书的全部路径。

    ``input`` 是组件扫描源文件的目录（库里只有这一个 ``source.*``），``output`` 是
    这本书自己的成品目录，``source`` 是书库里那份书信息（name/chapters/confident）。
    """

    paths = book_paths(service, str(book["id"]), str(book.get("extension") or ""))
    return {
        "book": book,
        "source": paths.meta,
        "active": paths.source.parent,
        "output": paths.output,
        "report": paths.report,
        "run": paths.run,
        "config": paths.config,
        "log": paths.log,
        "input": input_root(service),
        "uploads": input_root(service) / "uploads",
    }


def _selected_book(
    service: ReadingStudioService, requested: str | None
) -> dict[str, Any] | None:
    """GET 时惰性触发一次旧数据迁移（幂等），再按 选书→当前书→最近导入 解析。"""

    if not read_index(service)["books"]:
        migrate_legacy(service)
    return resolve_book(service, requested)


def _require_book(
    service: ReadingStudioService, requested: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    # 显式指定了一本不存在的书时直接 404：静默回退到别的书会把文件指错。
    if requested and book_entry(service, requested) is None:
        raise HTTPException(status_code=404, detail="这本书不在书库里")
    book = _selected_book(service, requested)
    if book is None:
        raise HTTPException(status_code=409, detail="书库里还没有小说，请先上传一本")
    return book, _paths(service, book)


def _catalog_size() -> int:
    catalog = _read_json(COMPONENT_ROOT / "data" / "vocabulary.json", [])
    return len(catalog) if isinstance(catalog, list) else 0


def _page_window(page: int, total: int, radius: int = 2) -> list[int | str]:
    """分页按钮：首末页 + 当前页附近，其余用省略号，总页数少时全列出。"""

    if total <= 7:
        return list(range(1, total + 1))
    numbers: list[int | str] = [1]
    start = max(2, page - radius)
    end = min(total - 1, page + radius)
    if start > 2:
        numbers.append("…")
    numbers.extend(range(start, end + 1))
    if end < total - 1:
        numbers.append("…")
    numbers.append(total)
    return numbers


def _chapter_outputs(
    output: Path | None,
    page: int,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int, int]:
    """已生成章节的当前页、总页数、总章数。"""

    chapters: list[dict[str, Any]] = []
    if output is not None:
        entries = _read_json(output / "index_entries.json", [])
        titles = {
            int(item[0]): str(item[1])
            for item in entries
            if isinstance(item, list) and len(item) == 2
        }
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
    selected_page = min(max(1, page), total_pages)
    start = (selected_page - 1) * page_size
    return chapters[start : start + page_size], total_pages, len(chapters)


def _page_context(
    service: ReadingStudioService,
    *,
    book: dict[str, Any] | None = None,
    page: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    books = list_books(service)
    selected = book or resolve_book(service, None)
    paths = _paths(service, selected) if selected else None
    output = paths["output"] if paths else None
    volume_files = (
        sorted((output / "volumes").glob("*.docx")) if output and output.exists() else []
    )
    chapter_outputs, total_pages, chapters_total = _chapter_outputs(output, page)
    library_files = []
    if output is not None and output.exists():
        library_files = [
            path
            for path in [
                output / "index.html",
                output / "glossary.xlsx",
                *sorted(output.glob("*.txt")),
            ]
            if path.is_file()
        ]
    migration = _read_json(output_root(service) / "migration_log.json")
    context = {
        "books": books,
        "selected": selected,
        "book_id": selected["id"] if selected else "",
        "source": selected,
        "report": _read_json(paths["report"]) if paths else None,
        "run": _read_json(paths["run"]) if paths else None,
        "progress": (
            progress_counts(output / "state.sqlite3")
            if output
            else {"completed": 0, "failed": 0, "running": 0}
        ),
        "catalog_size": _catalog_size(),
        "api_ready": service.config.deepseek_api_key is not None,
        "chapter_outputs": chapter_outputs,
        "page": min(page, total_pages),
        "total_pages": total_pages,
        "chapters_total": chapters_total,
        "page_numbers": _page_window(min(page, total_pages), total_pages),
        "volume_files": (
            [path.relative_to(output).as_posix() for path in volume_files[-12:]]
            if output
            else []
        ),
        "library_files": (
            [path.relative_to(output).as_posix() for path in library_files] if output else []
        ),
        "migration": migration,
    }
    context.update(extra)
    return context


@router.get("")
def novel_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    book: Annotated[str | None, Query()] = None,
):
    selected = _selected_book(service, book)
    return TEMPLATES.TemplateResponse(
        request,
        "novel/index.html",
        _page_context(service, book=selected, page=page),
    )


@router.post("/select")
def select_book(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    book: Annotated[str, Form()],
):
    """把某本书设为生成目标（当前书），查看与生成都以它为准。"""

    if book_entry(service, book) is None:
        raise HTTPException(status_code=404, detail="这本书不在书库里")
    set_active_book(service, book)
    return RedirectResponse(f"/novel?book={book}", status_code=303)


@router.post("/import")
async def import_novel(
    source: Annotated[UploadFile, File()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        extension = validate_extension(source.filename, ALLOWED_EXTENSIONS)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail="仅支持 TXT、DOCX 或 EPUB") from exc
    uploads = input_root(service) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    temporary = uploads / f".{os.urandom(8).hex()}.upload"
    digest = sha256()
    written = 0
    limit = service.config.web_max_upload_bytes
    try:
        with temporary.open("wb") as handle:
            first = True
            while chunk := await source.read(1024 * 1024):
                if first and not check_magic(extension, chunk[:8]):
                    raise HTTPException(
                        status_code=415,
                        detail=f"文件内容与扩展名 {extension} 不符，已拒绝",
                    )
                first = False
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件不能超过 {limit // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
                handle.write(chunk)
        # 原样留一份在 uploads/<sha256>/ 作为上传凭证，再把文件登记进书库（按 sha256 去重）。
        archive = uploads / digest.hexdigest() / f"source{extension}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, archive)
        book = register_book(
            service,
            digest=digest.hexdigest(),
            filename=Path(source.filename or archive.name).name,
            extension=extension,
            payload_path=archive,
            chapters=0,
            confident=False,
            bytes_written=written,
        )
        paths = _paths(service, book)
        try:
            result = parse_novel(paths["active"] / f"source{extension}")
        except ChapterDetectionError as exc:
            result = exc.result
        write_detection_report(result, paths["report"])
        entry = register_book(
            service,
            digest=digest.hexdigest(),
            filename=Path(source.filename or archive.name).name,
            extension=extension,
            payload_path=archive,
            chapters=len(result.chapters),
            confident=result.confident,
            bytes_written=written,
        )
    finally:
        await source.close()
        temporary.unlink(missing_ok=True)
    return RedirectResponse(f"/novel?book={entry['id']}", status_code=303)


def _source_file(book: dict[str, Any], paths: dict[str, Any]) -> Path:
    return paths["active"] / f"source{book.get('extension') or ''}"


@router.post("/estimate")
def estimate_novel(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    start: Annotated[int, Form(ge=1)] = 1,
    end: Annotated[int, Form(ge=1)] = 1,
    book: Annotated[str | None, Form()] = None,
):
    selected, paths = _require_book(service, book)
    if not selected.get("confident"):
        raise HTTPException(status_code=409, detail="请先导入并成功识别小说章节")
    result = parse_novel(_source_file(selected, paths))
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
        _page_context(
            service,
            book=selected,
            estimate=estimate,
            selected_start=start,
            selected_end=end,
        ),
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
    book: Annotated[str | None, Form()] = None,
):
    if confirmation.strip() != "确认生成样章":
        raise HTTPException(status_code=422, detail="请输入：确认生成样章")
    selected, paths = _require_book(service, book)
    _require_generation_ready(service, paths)
    if chapter > int(selected.get("chapters") or 0):
        raise HTTPException(status_code=422, detail="章节编号超出范围")
    _write_runtime_config(service, paths, batch_confirmed=False, max_chapters=1)
    description = f"《{selected['name']}》第 {chapter} 章样章"
    _write_json(paths["run"], {"status": "queued", "description": description})
    _queue_novel_batch(
        service,
        paths,
        arguments=["--chapter", str(chapter)],
        description=description,
        idempotency_key=f"novel-sample:{selected['id']}:{chapter}",
    )
    return RedirectResponse(f"/novel?book={selected['id']}", status_code=303)


@router.post("/generate-batch")
def generate_batch(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    start: Annotated[int, Form(ge=1)],
    end: Annotated[int, Form(ge=1)],
    confirmation: Annotated[str, Form()] = "",
    book: Annotated[str | None, Form()] = None,
):
    if confirmation.strip() != "确认批量生成":
        raise HTTPException(status_code=422, detail="请输入：确认批量生成")
    selected, paths = _require_book(service, book)
    _require_generation_ready(service, paths)
    if end < start or end > int(selected.get("chapters") or 0):
        raise HTTPException(status_code=422, detail="章节范围无效")
    if end - start + 1 > 20:
        raise HTTPException(status_code=422, detail="网页单次最多生成 20 章")
    _write_runtime_config(service, paths, batch_confirmed=True, max_chapters=20)
    description = f"《{selected['name']}》第 {start}–{end} 章批量任务"
    _write_json(paths["run"], {"status": "queued", "description": description})
    _queue_novel_batch(
        service,
        paths,
        arguments=["--start", str(start), "--end", str(end)],
        description=description,
        idempotency_key=f"novel-batch:{selected['id']}:{start}-{end}",
    )
    return RedirectResponse(f"/novel?book={selected['id']}", status_code=303)


@router.post("/recover/{action}")
def recover_batch(
    action: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    confirmation: Annotated[str, Form()] = "",
    book: Annotated[str | None, Form()] = None,
):
    if action not in {"resume", "retry-failed"}:
        raise HTTPException(status_code=404, detail="未知恢复操作")
    if confirmation.strip() != "确认继续生成":
        raise HTTPException(status_code=422, detail="请输入：确认继续生成")
    selected, paths = _require_book(service, book)
    _require_generation_ready(service, paths)
    _write_runtime_config(service, paths, batch_confirmed=True, max_chapters=20)
    label = "断点继续" if action == "resume" else "失败章节重试"
    description = f"《{selected['name']}》{label}"
    _write_json(paths["run"], {"status": "queued", "description": description})
    _queue_novel_batch(
        service,
        paths,
        arguments=[f"--{action}"],
        description=description,
        idempotency_key=f"novel-recover:{selected['id']}:{action}",
    )
    return RedirectResponse(f"/novel?book={selected['id']}", status_code=303)


@router.get("/files/{relative_path:path}")
def novel_file(
    relative_path: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    book: Annotated[str | None, Query()] = None,
):
    _selected, paths = _require_book(service, book)
    root = paths["output"].resolve()
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
    book: Annotated[str | None, Query()] = None,
):
    _selected, paths = _require_book(service, book)
    target = paths["output"] / "html" / f"第{chapter_id:04d}章.html"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="章节尚未生成")
    return FileResponse(target, media_type="text/html")


@router.get("/download/{chapter_id}/{format_name}")
def download_chapter(
    chapter_id: int,
    format_name: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    book: Annotated[str | None, Query()] = None,
):
    formats = {
        "html": ("html", ".html"),
        "docx": ("chapters", ".docx"),
        "json": ("chapter_json", ".json"),
    }
    if format_name not in formats:
        raise HTTPException(status_code=404, detail="未知文件格式")
    directory, suffix = formats[format_name]
    _selected, paths = _require_book(service, book)
    output = paths["output"]
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
