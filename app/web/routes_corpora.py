from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.pipeline.service import ReadingStudioService
from app.planning.boundaries import BoundaryEdit
from app.planning.importer import corpus_id_for

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")
ALLOWED_EXTENSIONS = {".txt", ".docx", ".epub", ".md", ".markdown"}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/corpora", status_code=303)


@router.get("/corpora")
def corpora_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/index.html",
        {"corpora": service.repository.list_corpora()},
    )


@router.get("/corpora/import")
def import_form(request: Request):
    return TEMPLATES.TemplateResponse(request, "corpora/import.html", {})


@router.post("/corpora/import")
async def import_upload(
    source: Annotated[UploadFile, File()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    extension = Path(source.filename or "").suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported source format")
    temporary_dir = service.config.input_dir / ".uploads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{uuid4().hex}.upload"
    destination: Path | None = None
    written = 0
    digest = sha256()
    try:
        with temporary.open("wb") as handle:
            while chunk := await source.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Upload exceeds 250 MB")
                digest.update(chunk)
                handle.write(chunk)
        upload_dir = service.config.input_dir / corpus_id_for(digest.hexdigest())
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / f"source{extension}"
        os.replace(temporary, destination)
        manifest = service.import_source(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if destination is not None and destination.exists():
            destination.unlink()
        raise
    finally:
        await source.close()
    return RedirectResponse(f"/corpora/{manifest.corpus.id}/preview", status_code=303)


@router.get("/corpora/{corpus_id}/preview")
def corpus_preview(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    corpus = service.repository.get_corpus(corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    chapters = service.repository.list_source_chapters(
        corpus_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/preview.html",
        {
            "corpus": corpus,
            "chapters": chapters,
            "page": page,
            "page_size": page_size,
            "unit_count": len(service.repository.list_units(corpus_id)),
        },
    )


@router.post("/corpora/{corpus_id}/boundaries")
def save_boundary_overrides(
    corpus_id: str,
    overrides: Annotated[str, Form()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        raw_edits = json.loads(overrides)
        if not isinstance(raw_edits, list):
            raise TypeError("Boundary overrides must be a JSON list")
        edits = [BoundaryEdit.model_validate(value) for value in raw_edits]
        service.apply_boundary_overrides(corpus_id, edits)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/corpora/{corpus_id}/preview", status_code=303)
