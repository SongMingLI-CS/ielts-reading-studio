import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.models import UnitStatus
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")


@router.get("/")
def dashboard(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    corpora = service.repository.list_corpora()
    completed = sum(
        unit.status == UnitStatus.COMPLETED
        for corpus in corpora
        for unit in service.repository.list_units(corpus.id)
    )
    novel_report_path = service.config.output_dir / "context-novel" / "reports" / "chapter_detection.json"
    novel_chapters = 0
    catalog_size = 0
    if novel_report_path.exists():
        try:
            novel_chapters = int(
                json.loads(novel_report_path.read_text(encoding="utf-8")).get(
                    "detected_chapters", 0
                )
            )
        except (OSError, ValueError, TypeError):
            pass
    catalog_path = (
        Path(__file__).parents[2]
        / "components"
        / "context-novel"
        / "data"
        / "vocabulary.json"
    )
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog_size = len(catalog) if isinstance(catalog, list) else 0
    except (OSError, ValueError):
        pass
    return TEMPLATES.TemplateResponse(
        request,
        "home.html",
        {
            "corpora_count": len(corpora),
            "reading_count": completed,
            "novel_chapters": novel_chapters,
            "catalog_size": catalog_size,
        },
    )
