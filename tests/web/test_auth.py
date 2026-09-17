from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.pipeline.service import ReadingStudioService
from app.web.app import create_app


def test_healthcheck_remains_public(web_service):
    protected_config = web_service.config.model_copy(
        update={"web_username": "reader", "web_password": SecretStr("secret")},
    )
    protected_service = ReadingStudioService(protected_config)

    with TestClient(create_app(service=protected_service)) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_web_interface_requires_valid_basic_auth(web_service):
    protected_config = web_service.config.model_copy(
        update={"web_username": "reader", "web_password": SecretStr("secret")},
    )
    protected_service = ReadingStudioService(protected_config)

    with TestClient(create_app(service=protected_service)) as client:
        denied = client.get("/practice")
        invalid = client.get("/practice", auth=("reader", "wrong"))
        allowed = client.get("/practice", auth=("reader", "secret"))

    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Basic")
    assert invalid.status_code == 401
    assert allowed.status_code == 200
