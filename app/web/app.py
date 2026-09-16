from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig
from app.pipeline.service import ReadingStudioService

from .routes_corpora import router as corpora_router
from .routes_jobs import router as jobs_router
from .routes_practice import router as practice_router


def create_app(
    config: AppConfig | None = None,
    service: ReadingStudioService | None = None,
) -> FastAPI:
    if service is None:
        selected = config or AppConfig.load(Path("config.yaml"))
        service = ReadingStudioService(selected)
    application = FastAPI(title="IELTS Reading Studio")
    application.state.service = service
    application.include_router(corpora_router)
    application.include_router(jobs_router)
    application.include_router(practice_router)
    static_dir = Path(__file__).parents[2] / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    return application
