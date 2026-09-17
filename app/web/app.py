from __future__ import annotations

import base64
import binascii
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Response
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

    @application.middleware("http")
    async def require_login(request: Request, call_next):
        username = service.config.web_username
        password = service.config.web_password
        if request.url.path == "/healthz" or not username or not password:
            return await call_next(request)
        supplied_username = ""
        supplied_password = ""
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                supplied_username, supplied_password = decoded.split(":", 1)
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pass
        valid_username = secrets.compare_digest(supplied_username, username)
        valid_password = secrets.compare_digest(
            supplied_password,
            password.get_secret_value(),
        )
        if not (valid_username and valid_password):
            return Response(
                "需要登录",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="IELTS Reading Studio", charset="UTF-8"'},
            )
        return await call_next(request)

    @application.get("/healthz", include_in_schema=False)
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(corpora_router)
    application.include_router(jobs_router)
    application.include_router(practice_router)
    static_dir = Path(__file__).parents[2] / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    return application
