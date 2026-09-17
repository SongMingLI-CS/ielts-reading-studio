from __future__ import annotations

import datetime as dt
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.requests import Request

from app.models import UnitStatus
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")

FORMAT_GUIDE: list[dict[str, str]] = [
    {
        "value": "html",
        "name": "HTML 单文件练习页",
        "who": "给自己或别人在手机、平板上做题",
        "what": "一个不依赖网络的 .html 文件，计时、判分、解析都在文件内部完成；交卷前界面不显示答案。",
        "keeps": "文章、题目、答案与解析、词汇",
    },
    {
        "value": "docx",
        "name": "DOCX 文档 / 练习册",
        "who": "要打印、批注或继续编辑",
        "what": "Word 文档，包含文章、题目、答案解析与词汇。选多篇时按「练习册」打包，每卷 20–50 篇。",
        "keeps": "文章、题目、答案与解析、词汇",
    },
    {
        "value": "json",
        "name": "JSON 规范数据",
        "who": "要二次加工或喂给别的程序",
        "what": "原始的 ReadingPackage，字段与数据库里完全一致，可被本项目或你自己的脚本重新读取。",
        "keeps": "全部字段（含来源覆盖、用量与质量报告）",
    },
]


def _exports_root(service: ReadingStudioService) -> Path:
    return service.config.output_dir / "exports"


def _relative_export_path(service: ReadingStudioService, path: Path) -> str:
    return str(path.relative_to(_exports_root(service)))


def _resolve_export(service: ReadingStudioService, relative: str) -> Path:
    """Resolve a download target, refusing anything outside output/exports."""
    root = _exports_root(service).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or not str(candidate).startswith(str(root)):
        raise HTTPException(status_code=404, detail="Export not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Export not found")
    return candidate


def _export_batches(service: ReadingStudioService) -> list[dict[str, Any]]:
    root = _exports_root(service)
    if not root.is_dir():
        return []
    batches: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        files = [path for path in sorted(directory.rglob("*")) if path.is_file()]
        if not files:
            continue
        batches.append(
            {
                "name": directory.name,
                "created": dt.datetime.fromtimestamp(
                    directory.stat().st_mtime, tz=dt.UTC
                ),
                "files": [
                    {
                        "name": path.name,
                        "relative": _relative_export_path(service, path),
                        "suffix": path.suffix.lstrip(".").upper() or "?",
                        "kb": max(1, round(path.stat().st_size / 1024)),
                    }
                    for path in files
                ],
                "total_kb": round(sum(path.stat().st_size for path in files) / 1024),
            }
        )
    return batches


def _completed_rows(service: ReadingStudioService) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for corpus in service.repository.list_corpora():
        for unit in service.repository.list_units(corpus.id):
            if unit.status != UnitStatus.COMPLETED:
                continue
            row: dict[str, Any] = {
                "id": unit.id,
                "ordinal": unit.ordinal,
                "difficulty": unit.difficulty.value,
                "title": None,
                "questions": None,
                "passed": None,
            }
            try:
                package = service.load_package(unit.id)
            except (FileNotFoundError, ValueError):
                rows.append(row)
                continue
            row.update(
                {
                    "title": package.passage.title,
                    "questions": sum(
                        len(group.questions) for group in package.question_groups
                    ),
                    "passed": package.quality_report.passed,
                }
            )
            rows.append(row)
    return rows


@router.get("/exports")
def export_center(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    created: str | None = None,
):
    units = _completed_rows(service)
    created_batch = created_count = None
    if created and ":" in created:
        created_batch, _, count = created.partition(":")
        created_count = count
    return TEMPLATES.TemplateResponse(
        request,
        "exports/index.html",
        {
            "units": units,
            "exportable": [unit for unit in units if unit["passed"] is not False],
            "blocked": [unit for unit in units if unit["passed"] is False],
            "batches": _export_batches(service),
            "formats": FORMAT_GUIDE,
            "created_batch": created_batch,
            "created_count": created_count,
            "exports_dir": _exports_root(service),
        },
    )


@router.post("/exports")
def create_exports(
    unit_ids: Annotated[list[str], Form()],
    format_name: Annotated[str, Form(alias="format")],
    service: Annotated[ReadingStudioService, Depends(get_service)],
    workbook_size: Annotated[int, Form(ge=20, le=50)] = 20,
):
    units = [service.repository.get_unit(unit_id) for unit_id in unit_ids]
    if any(unit is None or unit.status != UnitStatus.COMPLETED for unit in units):
        raise HTTPException(
            status_code=409, detail="Only completed units can be exported"
        )
    try:
        packages = [service.load_package(unit_id) for unit_id in unit_ids]
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if any(not package.quality_report.passed for package in packages):
        raise HTTPException(
            status_code=409, detail="Only validated packages can be exported"
        )
    batch = f"manual-{uuid4().hex[:8]}"
    destination = _exports_root(service) / batch
    if format_name == "docx" and len(packages) > 1:
        paths = service.exporter.export_workbooks(packages, destination, workbook_size)
    elif format_name in {"json", "html", "docx"}:
        paths = [
            path
            for package in packages
            for path in service.exporter.export_package(
                package, destination, {format_name}
            )
        ]
    else:
        raise HTTPException(status_code=422, detail="Unsupported export format")
    return RedirectResponse(f"/exports?created={batch}:{len(paths)}", status_code=303)


@router.get("/exports/file/{relative:path}")
def download_export(
    relative: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    path = _resolve_export(service, relative)
    return FileResponse(path, filename=path.name, media_type=_media_type(path))


@router.get("/exports/bundle/{batch}")
def download_export_batch(
    batch: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    root = _exports_root(service).resolve()
    directory = (root / Path(batch).name).resolve()
    if not directory.is_dir() or directory.parent != root:
        raise HTTPException(status_code=404, detail="Export batch not found")
    files = [path for path in sorted(directory.rglob("*")) if path.is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="Export batch is empty")
    handle, name = tempfile.mkstemp(prefix="ielts-export-", suffix=".zip")
    os.close(handle)
    archive_path = Path(name)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{directory.name}/{path.name}")
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{directory.name}.zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


def _media_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".json": "application/json",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".zip": "application/zip",
    }.get(path.suffix.casefold(), "application/octet-stream")
