from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agents.base import AgentSchemaError, ProviderError
from app.config import redact_secrets
from app.learning.catalog import CATALOG, fetch_collection
from app.learning.models import Topic
from app.learning.service import LearningService
from app.learning.sources import (
    fetch_official_document,
    parse_markdown,
    validate_official_url,
)
from app.pipeline.service import ReadingStudioService
from app.security.budget import BudgetError

from .dependencies import get_service
from .templating import templates

router = APIRouter(tags=["learning"])
TEMPLATES = templates()
STARTERS = list(CATALOG.values())
READING_PAGE_CHARACTERS = 60000


def upload_limit(service):
    config = service.studio.config
    return min(config.web_learning_max_upload_bytes, config.web_max_upload_bytes)


def learning(
    studio: Annotated[ReadingStudioService, Depends(get_service)],
) -> LearningService:
    return LearningService(studio)


Learning = Annotated[LearningService, Depends(learning)]


def library(request, service, *, error="", status_code=200):
    documents = service.list_documents()
    progress = {
        doc.id: sum(bool(p.get("completed")) for p in service.progress(doc.id).values())
        for doc in documents
    }
    return TEMPLATES.TemplateResponse(
        request,
        "learning/index.html",
        {
            "documents": documents,
            "starters": STARTERS,
            "progress": progress,
            "error": error,
            "upload_limit_mb": upload_limit(service) // (1024 * 1024),
        },
        status_code=status_code,
    )


@router.get("/learn")
def index(request: Request, service: Learning):
    return library(request, service)


@router.post("/learn/import")
def import_url(
    request: Request, service: Learning, url: Annotated[str, Form(max_length=2000)]
):
    try:
        doc = service.save_document(fetch_official_document(url))
    except (ValueError, UnicodeError) as exc:
        return library(request, service, error=str(exc), status_code=400)
    except httpx.HTTPError:
        return library(
            request,
            service,
            error="暂时无法读取官方文档，请稍后重试，或导入已保存的 Markdown。",
            status_code=502,
        )
    return RedirectResponse(f"/learn/{doc.id}", status_code=303)


@router.post("/learn/upload")
async def upload(
    request: Request,
    service: Learning,
    source: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    topic: Annotated[Topic, Form()],
    version: Annotated[str, Form(min_length=1, max_length=100)],
    source_url: Annotated[str, Form(max_length=2000)] = "",
):
    try:
        if not (source.filename or "").lower().endswith((".md", ".markdown")):
            raise HTTPException(415, "技术文档文件请使用 .md 或 .markdown 格式。")
        limit = upload_limit(service)
        if source.size is not None and source.size > limit:
            raise HTTPException(413, f"文档不能超过 {limit // (1024 * 1024)} MB。")
        chunks = []
        size = 0
        while chunk := await source.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"文档不能超过 {limit // (1024 * 1024)} MB。")
            chunks.append(chunk)
        data = b"".join(chunks)
        chunks.clear()
        if source_url:
            source_url = validate_official_url(source_url)
        if not title.strip() or not version.strip():
            raise ValueError("请填写标题和版本；未知版本可填写“未标注”。")

        def save():
            return service.save_document(
                parse_markdown(
                    data.decode("utf-8-sig"),
                    title=title.strip(),
                    topic=topic,
                    version=version.strip(),
                    source_url=source_url,
                )
            )

        doc = await run_in_threadpool(save)
    except HTTPException as exc:
        return library(request, service, error=exc.detail, status_code=exc.status_code)
    except (ValueError, UnicodeError) as exc:
        return library(request, service, error=str(exc), status_code=400)
    finally:
        await source.close()
    return RedirectResponse(f"/learn/{doc.id}", status_code=303)


@router.post("/learn/collection")
def import_collection(
    request: Request, service: Learning, topic: Annotated[Topic, Form()]
):
    try:
        doc = service.save_document(fetch_collection(topic))
    except (ValueError, UnicodeError) as exc:
        return library(request, service, error=str(exc), status_code=400)
    except httpx.HTTPError:
        return library(
            request,
            service,
            error="学习路径暂时无法完整下载，未保存不完整内容。请稍后重试。",
            status_code=502,
        )
    return RedirectResponse(f"/learn/{doc.id}", status_code=303)


@router.get("/learn/{document_id}")
def reader(
    request: Request,
    document_id: str,
    service: Learning,
    section: str | None = None,
    start: str | None = None,
):
    try:
        doc = service.get_document(document_id)
        selected = next(
            s for s in doc.sections if s.id == (section or start or doc.sections[0].id)
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except StopIteration:
        raise HTTPException(404, "章节不存在") from None
    index = doc.sections.index(selected)
    continuous = section is None
    page_sections = [selected]
    page_start = index
    if continuous:
        # Stable windows let the previous-page link recover exactly the same text.
        windows = []
        current, count = [], 0
        for position, item in enumerate(doc.sections):
            length = len(item.text)
            if current and (
                count + length > READING_PAGE_CHARACTERS or len(current) >= 40
            ):
                windows.append(current)
                current, count = [], 0
            current.append(position)
            count += length
        if current:
            windows.append(current)
        window_index = next(i for i, w in enumerate(windows) if index in w)
        window = windows[window_index]
        page_start = window[0]
        page_sections = [doc.sections[i] for i in window]
        previous = doc.sections[windows[window_index - 1][0]] if window_index else None
        following = (
            doc.sections[windows[window_index + 1][0]]
            if window_index + 1 < len(windows)
            else None
        )
    else:
        previous = doc.sections[index - 1] if index else None
        following = doc.sections[index + 1] if index + 1 < len(doc.sections) else None
    return TEMPLATES.TemplateResponse(
        request,
        "learning/reader.html",
        {
            "doc": doc,
            "section": selected,
            "continuous": continuous,
            "page_sections": page_sections,
            "page_start": page_start,
            "progress": service.progress(doc.id),
            "guide": service.get_guide(doc.id, selected.id) if not continuous else None,
            "previous": previous,
            "next_section": following,
        },
    )


@router.post("/api/learn/{document_id}/{section_id}/guide")
def guide(document_id: str, section_id: str, service: Learning):
    try:
        return service.generate_guide(document_id, section_id)
    except KeyError:
        raise HTTPException(404, "文档或章节不存在") from None
    except BudgetError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except (ProviderError, AgentSchemaError) as exc:
        raise HTTPException(502, redact_secrets(exc)) from None


@router.post("/api/learn/{document_id}/{section_id}/vocabulary")
def vocabulary(document_id: str, section_id: str, service: Learning):
    try:
        return service.generate_vocabulary(document_id, section_id)
    except KeyError:
        raise HTTPException(404, "文档或章节不存在") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except BudgetError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except (ProviderError, AgentSchemaError) as exc:
        raise HTTPException(502, redact_secrets(exc)) from None


class ProgressChange(BaseModel):
    completed: bool | None = None
    notes: str | None = Field(None, max_length=4000)


@router.post("/api/learn/{document_id}/{section_id}/progress")
def progress(
    document_id: str, section_id: str, body: ProgressChange, service: Learning
):
    try:
        return service.update_progress(
            document_id, section_id, body.model_dump(exclude_none=True)
        )
    except KeyError:
        raise HTTPException(404, "文档或章节不存在") from None


class QuizAnswers(BaseModel):
    answers: list[int] = Field(min_length=2, max_length=8)


@router.post("/api/learn/{document_id}/{section_id}/quiz")
def quiz(document_id: str, section_id: str, body: QuizAnswers, service: Learning):
    try:
        return service.submit_quiz(document_id, section_id, body.answers)
    except KeyError:
        raise HTTPException(404, "文档或章节不存在") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
