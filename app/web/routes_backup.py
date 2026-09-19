from __future__ import annotations

import datetime as dt
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.requests import Request

from app.pipeline.service import ReadingStudioService
from app.storage.snapshot import snapshot_database

from .dependencies import get_service
from .templating import templates

router = APIRouter()
TEMPLATES = templates()


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _file_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for child in path.rglob("*") if child.is_file())


def _backup_facts(service: ReadingStudioService) -> dict[str, Any]:
    database = service.config.database_path
    output_dir = service.config.output_dir
    input_dir = service.config.input_dir
    return {
        "database": database,
        "database_bytes": _path_size(database),
        "output_dir": output_dir,
        "output_bytes": _path_size(output_dir),
        "output_files": _file_count(output_dir),
        "input_dir": input_dir,
        "input_bytes": _path_size(input_dir),
        "input_files": _file_count(input_dir),
        "total_bytes": _path_size(database) + _path_size(output_dir) + _path_size(input_dir),
    }


def _clean_stale_archives(max_age_seconds: int = 3600) -> None:
    """Drop temp archives left behind by interrupted downloads."""
    cutoff = dt.datetime.now(dt.UTC).timestamp() - max_age_seconds
    for pattern in ("ielts-backup-*.zip", "ielts-export-*.zip"):
        for path in Path(tempfile.gettempdir()).glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


def _snapshot_database(database: Path, target: Path) -> None:
    """Copy the SQLite file through the backup API so WAL content is included."""
    snapshot_database(database, target)


@router.get("/backup")
def backup_page(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    facts = _backup_facts(service)
    return TEMPLATES.TemplateResponse(
        request,
        "backup/index.html",
        {
            "facts": facts,
            "total_mb": round(facts["total_bytes"] / 1024 / 1024, 1),
            "database_mb": round(facts["database_bytes"] / 1024 / 1024, 1),
            "output_mb": round(facts["output_bytes"] / 1024 / 1024, 1),
            "input_mb": round(facts["input_bytes"] / 1024 / 1024, 1),
        },
    )


@router.get("/backup/download")
def backup_download(
    background: BackgroundTasks,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Stream one zip with a consistent database snapshot plus every artifact.

    Secrets are deliberately outside the archive: only state.db, output/ and
    input/ are included, never .env or .env.web.
    """
    del background
    facts = _backup_facts(service)
    _clean_stale_archives()
    now = dt.datetime.now(dt.UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    handle, name = tempfile.mkstemp(prefix="ielts-backup-", suffix=".zip")
    os.close(handle)
    archive_path = Path(name)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        database_copy = archive_path.with_name(f"{archive_path.name}.db")
        _snapshot_database(facts["database"], database_copy)
        if database_copy.exists():
            archive.write(database_copy, "state.db")
            database_copy.unlink()
        for root in (facts["output_dir"], facts["input_dir"]):
            if not root.is_dir():
                continue
            base = root.name
            for child in sorted(root.rglob("*")):
                if not child.is_file():
                    continue
                if child.name.endswith(("-wal", "-shm")):
                    continue  # covered by the snapshot
                if ".uploads" in child.parts:
                    continue
                archive.write(child, f"{base}/{child.relative_to(root)}")
        archive.writestr(
            "BACKUP-README.txt",
            "\n".join(
                [
                    "IELTS Reading Studio backup",
                    f"created: {now.isoformat(timespec='seconds')}",
                    "",
                    "included : state.db (consistent snapshot), output/, input/",
                    "excluded : .env, .env.web and any other secret file",
                    "",
                    "restore  : stop the service, unpack this archive over the project",
                    "           directory, then start the service again.",
                ]
            ),
        )

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"ielts-reading-studio-{stamp}.zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )
