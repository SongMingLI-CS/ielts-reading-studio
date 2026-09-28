from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.agents.base import AgentSchemaError, ProviderError
from app.config import redact_secrets
from app.learning.models import Topic
from app.learning.service import LearningService
from app.learning.sources import (
    MAX_SOURCE_BYTES,
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
STARTERS = [
    {
        "topic": "python",
        "code": "01 / PYTHON",
        "title": "Python 基础",
        "description": "从变量、字符串和列表开始，习惯读懂代码与英语说明。",
        "url": "https://docs.python.org/3/tutorial/introduction.html",
    },
    {
        "topic": "numpy",
        "code": "02 / NUMPY",
        "title": "NumPy 数组",
        "description": "理解 shape、dtype、索引与数组运算，建立数据计算基础。",
        "url": "https://numpy.org/doc/stable/user/absolute_beginners.html",
    },
    {
        "topic": "ai",
        "code": "03 / AI",
        "title": "机器学习入门",
        "description": "阅读 scikit-learn 的训练、预测与评估流程，连接前两阶段。",
        "url": "https://scikit-learn.org/stable/getting_started.html",
    },
]


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
        data = await source.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise HTTPException(413, "文档不能超过 2 MB。")
        if source_url:
            source_url = validate_official_url(source_url)
        if not title.strip() or not version.strip():
            raise ValueError("请填写标题和版本；未知版本可填写“未标注”。")
        doc = service.save_document(
            parse_markdown(
                data.decode("utf-8-sig"),
                title=title.strip(),
                topic=topic,
                version=version.strip(),
                source_url=source_url,
            )
        )
    except (ValueError, UnicodeError) as exc:
        return library(request, service, error=str(exc), status_code=400)
    finally:
        await source.close()
    return RedirectResponse(f"/learn/{doc.id}", status_code=303)


@router.get("/learn/{document_id}")
def reader(
    request: Request, document_id: str, service: Learning, section: str | None = None
):
    try:
        doc = service.get_document(document_id)
        _, selected = service.section(document_id, section or doc.sections[0].id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    index = doc.sections.index(selected)
    return TEMPLATES.TemplateResponse(
        request,
        "learning/reader.html",
        {
            "doc": doc,
            "section": selected,
            "progress": service.progress(doc.id),
            "guide": service.get_guide(doc.id, selected.id),
            "previous": doc.sections[index - 1] if index else None,
            "next_section": doc.sections[index + 1]
            if index + 1 < len(doc.sections)
            else None,
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
