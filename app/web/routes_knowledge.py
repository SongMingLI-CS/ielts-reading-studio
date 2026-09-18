from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.knowledge import VIEWS, build_digest, highlight
from app.knowledge.export import to_markdown
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")
MAX_PRINT_ROWS = 200


def _filters(
    view: str,
    source: str,
    kind: str,
    level: str,
    pos: str,
    q: str,
    has_collocation: bool,
    repeats: bool,
    sort: str,
    size: int,
    page: int,
) -> dict[str, Any]:
    return {
        "view": view,
        "source": source,
        "kind": kind,
        "level": level,
        "pos": pos,
        "q": q,
        "has_collocation": has_collocation,
        "repeats": repeats,
        "sort": sort,
        "size": size,
        "page": page,
    }


def _query_string(filters: dict[str, Any]) -> str:
    """把当前筛选条件编成查询串，供分页/打印/导出链接复用（保持视图一致）。"""
    pairs: list[tuple[str, str]] = [("view", str(filters.get("view") or "terms"))]
    for key in ("source", "kind", "level", "pos", "sort"):
        value = filters.get(key)
        if value and value != "all":
            pairs.append((key, str(value)))
    if filters.get("q"):
        pairs.append(("q", str(filters["q"])))
    if filters.get("has_collocation"):
        pairs.append(("has_collocation", "true"))
    if filters.get("repeats"):
        pairs.append(("repeats", "true"))
    if filters.get("size"):
        pairs.append(("size", str(filters["size"])))
    return urlencode(pairs)


def _decorate(digest: dict[str, Any]) -> dict[str, Any]:
    """给每条数据补上"例句里高亮目标表达"的 HTML，模板只负责排版。"""
    if digest["view"] == "paraphrase":
        for note in digest["rows"]:
            note["prompt_html"] = highlight(
                note["prompt"], note["prompt_markers"], "kw-sub"
            )
            note["quote_html"] = highlight(
                note["evidence_quote"], note["quote_markers"], "kw-src"
            )
        return digest
    for row in digest["rows"]:
        terms = [row["headword"], *row.get("collocations", [])]
        if row.get("example"):
            row["example_html"] = highlight(row["example"], terms)
        if digest["view"] == "collocations" and row.get("word"):
            # 搭配卡片里把"词"本身标出来，便于看出搭配是围着哪个词转的
            row["headword_html"] = highlight(row["headword"], [row["word"]], "kw-coll")
        for context in row.get("contexts", []):
            context["en_html"] = highlight(context["en"], [row["headword"]])
    return digest


@router.get("/knowledge")
def knowledge_home(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    view: str = "terms",
    source: str = "all",
    kind: str = "all",
    level: str = "all",
    pos: str = "all",
    q: str = "",
    has_collocation: bool = False,
    repeats: bool = False,
    sort: str = "alpha",
    size: int = 0,
    page: int = 1,
):
    digest = _decorate(
        build_digest(
            service,
            **_filters(
                view,
                source,
                kind,
                level,
                pos,
                q,
                has_collocation,
                repeats,
                sort,
                size,
                page,
            ),
        )
    )
    return TEMPLATES.TemplateResponse(
        request,
        "knowledge/index.html",
        {
            "digest": digest,
            "filters": digest["filters"],
            "base_query": _query_string(digest["filters"]),
            "view_queries": {
                view: _query_string({**digest["filters"], "view": view})
                for view in VIEWS
            },
        },
    )


@router.get("/knowledge/print")
def knowledge_print(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    view: str = "terms",
    source: str = "all",
    kind: str = "all",
    level: str = "all",
    pos: str = "all",
    q: str = "",
    has_collocation: bool = False,
    repeats: bool = False,
    sort: str = "alpha",
    size: int = MAX_PRINT_ROWS,
    page: int = 1,
):
    digest = _decorate(
        build_digest(
            service,
            **_filters(
                view,
                source,
                kind,
                level,
                pos,
                q,
                has_collocation,
                repeats,
                sort,
                min(size or MAX_PRINT_ROWS, MAX_PRINT_ROWS),
                page,
            ),
        )
    )
    return TEMPLATES.TemplateResponse(
        request,
        "knowledge/print.html",
        {
            "digest": digest,
            "filters": digest["filters"],
            "max_rows": MAX_PRINT_ROWS,
            "base_query": _query_string(digest["filters"]),
        },
    )


@router.get("/knowledge/export.md")
def knowledge_markdown(
    service: Annotated[ReadingStudioService, Depends(get_service)],
    view: str = "terms",
    source: str = "all",
    kind: str = "all",
    level: str = "all",
    pos: str = "all",
    q: str = "",
    has_collocation: bool = False,
    repeats: bool = False,
    sort: str = "alpha",
    size: int = 0,
    page: int = 1,
):
    digest = build_digest(
        service,
        **_filters(
            view,
            source,
            kind,
            level,
            pos,
            q,
            has_collocation,
            repeats,
            sort,
            size,
            page,
        ),
    )
    safe_view = digest["view"]
    return Response(
        content=to_markdown(digest),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="knowledge-{safe_view}.md"'
        },
    )
