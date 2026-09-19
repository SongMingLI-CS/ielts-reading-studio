from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig
from app.pipeline.service import ReadingStudioService
from app.security.sessions import SESSION_COOKIE
from app.security.settings import build_security_runtime

from .routes_backup import router as backup_router
from .routes_corpora import router as corpora_router
from .routes_exports import router as exports_router
from .routes_home import router as home_router
from .routes_jobs import router as jobs_router
from .routes_knowledge import router as knowledge_router
from .routes_novel import router as novel_router
from .routes_practice import router as practice_router
from .routes_review import router as review_router
from .routes_settings import router as settings_router
from .routes_vocabulary import router as vocabulary_router
from .routes_writing import router as writing_router
from .security_gate import SecurityGate


def create_app(
    config: AppConfig | None = None,
    service: ReadingStudioService | None = None,
) -> FastAPI:
    if service is None:
        selected = config or AppConfig.load(Path("config.yaml"))
        service = ReadingStudioService(selected)
    application = FastAPI(title="IELTS Reading Studio")
    application.state.service = service
    # Sessions, CSRF, rate limits and security headers all live behind this gate.
    security = build_security_runtime(service.config, service.database)
    application.state.security = security
    application.middleware("http")(SecurityGate(service.config, security))

    @application.get("/healthz", include_in_schema=False)
    def healthcheck() -> dict[str, str]:
        """Liveness only: no version, path or counter information."""

        return {"status": "ok"}

    @application.get("/api/csrf-token", include_in_schema=False)
    def csrf_token(request: Request) -> dict[str, str]:
        """CSRF token for programmatic clients; it pairs with the session cookie.

        Send the credentials first, keep the ``ielts_session`` cookie that comes back,
        then pass the token in ``X-CSRF-Token`` on every unsafe request.
        """

        return {"token": request.state.csrf_token}

    @application.post("/logout", include_in_schema=False)
    def logout(request: Request) -> Response:
        """Invalidate the session server-side and clear the cookie."""

        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            security.sessions.destroy(session_id)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    application.include_router(home_router)
    application.include_router(corpora_router)
    application.include_router(jobs_router)
    application.include_router(practice_router)
    application.include_router(knowledge_router)
    application.include_router(exports_router)
    application.include_router(novel_router)
    application.include_router(backup_router)
    application.include_router(review_router)
    application.include_router(vocabulary_router)
    application.include_router(settings_router)
    application.include_router(writing_router)
    static_dir = Path(__file__).parents[2] / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    return application


    application.include_router(home_router)
    application.include_router(corpora_router)
    application.include_router(jobs_router)
    application.include_router(practice_router)
    application.include_router(knowledge_router)
    application.include_router(exports_router)
    application.include_router(novel_router)
    application.include_router(backup_router)
    application.include_router(review_router)
    application.include_router(vocabulary_router)
    application.include_router(settings_router)
    application.include_router(writing_router)
    static_dir = Path(__file__).parents[2] / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    return application
