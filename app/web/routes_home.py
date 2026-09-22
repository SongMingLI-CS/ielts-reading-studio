import datetime as dt
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.models import Corpus, GenerationUnit, ReadingPackage, UnitStatus
from app.pipeline.service import ReadingStudioService
from app.vocabulary import due_rows, study_rows

from .dependencies import get_service

# 复习待办必须与错题页用同一份判定，否则首页和错题页会对同一批作答给出不同数字。
from .routes_practice import mistake_items
from .templating import templates

router = APIRouter()
TEMPLATES = templates()


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
            "next_step": next_step(service),
            "review": review_workload(service),
        },
    )


def next_step(service: ReadingStudioService) -> dict[str, Any]:
    """首页只回答一个问题：现在该点哪里。

    有草稿就先继续草稿——而且**不能**带 ``?fresh=1``，那会丢掉已经写好的答案；
    没有草稿就推荐第一篇没做过的；全部都做过就重做最近一篇；一篇都没有则引导导入。
    """

    attempts = service.repository.list_practice_attempts()
    for attempt in attempts:
        if attempt["status"] != "in_progress":
            continue
        card = _resume_card(service, attempt)
        if card is not None:
            return card
    attempted = {attempt["unit_id"] for attempt in attempts}
    library = _library(service)
    for corpus, unit in library:
        if unit.id not in attempted:
            card = _unit_card(service, corpus, unit, kind="start", cta="开始练习 →")
            if card is not None:
                return card
    for attempt in attempts:
        match = next((pair for pair in library if pair[1].id == attempt["unit_id"]), None)
        if match is not None:
            card = _unit_card(
                service,
                match[0],
                match[1],
                kind="redo",
                cta="重新开始 →",
                href_suffix="?fresh=1",
            )
            if card is not None:
                return card
    return {"kind": "empty"}


def review_workload(service: ReadingStudioService) -> dict[str, int]:
    """今天有什么在等你：错题、生词本、到期复习词。全部来自真实记录。"""

    vocabulary = study_rows(service)
    return {
        "mistakes": len(mistake_items(service)),
        "words": sum(1 for row in vocabulary if row["status"] == "saved"),
        "due": len(due_rows(vocabulary, dt.datetime.now(dt.UTC))),
    }


def _library(service: ReadingStudioService) -> list[tuple[Corpus, GenerationUnit]]:
    """已完成、可练习的单元，按语料与章节顺序。"""

    return [
        (corpus, unit)
        for corpus in service.repository.list_corpora()
        for unit in service.repository.list_units(corpus.id)
        if unit.status == UnitStatus.COMPLETED
    ]


def _question_numbers(package: ReadingPackage) -> list[int]:
    return [
        question.number
        for group in package.question_groups
        for question in group.questions
    ]


def _has_answer(value: Any) -> bool:
    """草稿里这一题算不算答过。多选存的是列表，文本题存字符串。"""

    if isinstance(value, (list, tuple, set)):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def _resume_card(
    service: ReadingStudioService, attempt: dict[str, Any]
) -> dict[str, Any] | None:
    """草稿卡：标明答到第几题、还剩多少、已经花了多久。"""

    unit = service.repository.get_unit(attempt["unit_id"])
    if unit is None or unit.status != UnitStatus.COMPLETED:
        return None
    try:
        package = service.load_package(unit.id)
    except (FileNotFoundError, ValueError):
        return None
    numbers = _question_numbers(package)
    answers = attempt["payload"].get("answers") or {}
    answered = sum(1 for number in numbers if _has_answer(answers.get(str(number))))
    upcoming = next(
        (number for number in numbers if not _has_answer(answers.get(str(number)))),
        None,
    )
    elapsed = attempt["payload"].get("elapsed_seconds")
    corpus = service.repository.get_corpus(unit.corpus_id)
    return {
        "kind": "resume",
        "unit_id": unit.id,
        "title": package.passage.title,
        "source": corpus.name if corpus else "",
        "href": f"/practice/{unit.id}",
        "cta": f"继续第 {upcoming} 题 →" if upcoming is not None else "继续作答 →",
        "answered": answered,
        "total": len(numbers),
        "elapsed": elapsed if isinstance(elapsed, int) and elapsed > 0 else 0,
        "question_count": len(numbers),
        "word_count": package.passage.word_count,
    }


def _unit_card(
    service: ReadingStudioService,
    corpus: Corpus,
    unit: GenerationUnit,
    *,
    kind: str,
    cta: str,
    href_suffix: str = "",
) -> dict[str, Any] | None:
    """没有草稿时的卡片：推荐一篇没做过的，或重做最近一篇。"""

    try:
        package = service.load_package(unit.id)
    except (FileNotFoundError, ValueError):
        return None
    numbers = _question_numbers(package)
    return {
        "kind": kind,
        "unit_id": unit.id,
        "title": package.passage.title,
        "source": corpus.name,
        "href": f"/practice/{unit.id}{href_suffix}",
        "cta": cta,
        "answered": 0,
        "total": len(numbers),
        "elapsed": 0,
        "question_count": len(numbers),
        "word_count": package.passage.word_count,
    }
