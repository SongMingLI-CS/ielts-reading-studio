"""Authenticated, idempotent context handoff to a configured ScholarKernel."""
from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.config import AppConfig
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()


def configured_urls(config: AppConfig) -> tuple[str, str] | None:
    public = config.scholar_kernel_url.rstrip("/")
    api = (config.scholar_kernel_api_url or public).rstrip("/")
    if not config.scholar_kernel_bridge_token:
        return None
    for value in (public, api):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            return None
    return public, api


class ScholarImport(BaseModel):
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=12_000)
    question: str = Field(min_length=1, max_length=2_000)
    source_path: str = Field(min_length=1, max_length=1_500)

    @field_validator("title", "text", "question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("source_path")
    @classmethod
    def relative_source(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
            raise ValueError("来源必须是当前网站中的路径")
        return value


@router.post("/api/scholar/import")
def import_context(
    body: ScholarImport,
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    urls = configured_urls(service.config)
    if not urls:
        return JSONResponse({"detail": "ScholarKernel 尚未连接，请先配置服务地址与连接密钥。"}, status_code=503)
    public, api = urls
    token = service.config.scholar_kernel_bridge_token
    assert token is not None
    try:
        response = httpx.post(
            api + "/api/integrations/learning-studio",
            headers={"Authorization": "Bearer " + token.get_secret_value()},
            json={
                "requestId": str(body.request_id),
                "title": body.title,
                "text": body.text,
                "question": body.question,
                "sourceUrl": str(request.base_url).rstrip("/") + body.source_path,
            },
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
        )
        response.raise_for_status()
        conversation_id = response.json()["conversationId"]
        if not isinstance(conversation_id, str) or not re.fullmatch(r"learning-[0-9a-f-]{36}", conversation_id):
            raise ValueError("invalid conversation")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return JSONResponse({"detail": "暂时无法连接 ScholarKernel。内容仍保留在这里，可以重试。"}, status_code=502)
    return JSONResponse(
        {"url": public + "/?" + urlencode({"conversation": conversation_id, "learning": "1"})},
        status_code=201,
    )
