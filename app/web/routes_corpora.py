from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.models import Corpus, GenerationUnit, UnitStatus
from app.pipeline.service import ReadingStudioService
from app.planning.boundaries import BoundaryEdit
from app.planning.importer import MANIFEST_REBUILT_DIAGNOSTIC, corpus_id_for
from app.security.uploads import check_magic, validate_extension

from .dependencies import get_service
from .glossary import job_status_label
from .templating import templates

router = APIRouter()
TEMPLATES = templates()
ALLOWED_EXTENSIONS = {".txt", ".docx", ".epub", ".md", ".markdown"}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
METADATA_JUNK = re.compile(
    r"[\(\[]?\s*(z-library|z-lib|1lib|libgen|annas-archive|sk|www)\b[^\)\]]*[\)\]]?",
    re.IGNORECASE,
)
SEPARATORS = re.compile(r"[\s_]+")
DIAGNOSTIC_LABELS: dict[str, str] = {
    "no_reliable_boundaries": "没有找到可靠的章节边界，无法建立生成单元。",
    "ordinal_gap": "章节序号不连续，可能有章节被漏识别。",
    "low_confidence_structure": "章节结构置信度偏低，边界可能不准。",
    "empty_chapters": "存在空章节（可能只有标题没有正文）。",
    "large_preamble": "正文开始前有较长的前言/目录，占比偏高。",
    "manifest_rebuilt_from_index": "章节清单已按数据库索引重建，原始解析置信度未记录。",
}
DIAGNOSTIC_PREFIX_LABELS: dict[str, str] = {
    "usable_chapters": "可用章节数",
    "empty_chapters": "空章节数",
}


@router.get("/corpora")
def corpora_index(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    deleted: str | None = None,
):
    cards = [_corpus_card(service, corpus) for corpus in service.repository.list_corpora()]
    cards.sort(key=lambda card: (card["source_exists"] is False, -card["unit_count"]))
    totals = {
        "corpora": len(cards),
        "chapters": sum(card["corpus"].chapter_count for card in cards),
        "units": sum(card["unit_count"] for card in cards),
        "completed": sum(card["statuses"].get(UnitStatus.COMPLETED.value, 0) for card in cards),
        "needs_review": sum(
            card["statuses"].get(UnitStatus.NEEDS_REVIEW.value, 0) for card in cards
        ),
        "missing_sources": sum(1 for card in cards if not card["source_exists"]),
    }
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/index.html",
        {"cards": cards, "totals": totals, "deleted": deleted},
    )


@router.get("/corpora/import")
def import_form(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/import.html",
        {"max_mb": MAX_UPLOAD_BYTES // (1024 * 1024)},
    )


@router.post("/corpora/import")
async def import_upload(
    source: Annotated[UploadFile, File()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        extension = validate_extension(source.filename, ALLOWED_EXTENSIONS)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    limit = service.config.web_max_upload_bytes
    temporary_dir = service.config.input_dir / ".uploads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{uuid4().hex}.upload"
    destination: Path | None = None
    written = 0
    digest = sha256()
    try:
        with temporary.open("wb") as handle:
            first = True
            while chunk := await source.read(1024 * 1024):
                if first and not check_magic(extension, chunk[:8]):
                    raise HTTPException(
                        status_code=415,
                        detail=f"文件内容与扩展名 {extension} 不符，已拒绝",
                    )
                first = False
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件不能超过 {limit // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
                handle.write(chunk)
        upload_dir = service.config.input_dir / corpus_id_for(digest.hexdigest())
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / f"source{extension}"
        os.replace(temporary, destination)
        manifest = service.import_source(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if destination is not None and destination.exists():
            destination.unlink()
        raise
    finally:
        await source.close()
    return RedirectResponse(f"/corpora/{manifest.corpus.id}/preview", status_code=303)


@router.post("/corpora/{corpus_id}/rebind")
async def rebind_source(
    corpus_id: str,
    source: Annotated[UploadFile, File()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    """Re-attach a new copy of a corpus source file.

    The corpus identity is the SHA-256 of its bytes, so uploading the same file
    again only repairs the stored path and keeps every existing artifact.
    """
    corpus = service.repository.get_corpus(corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    try:
        extension = validate_extension(source.filename, ALLOWED_EXTENSIONS)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    limit = service.config.web_max_upload_bytes
    temporary_dir = service.config.input_dir / ".uploads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f"{uuid4().hex}.upload"
    written = 0
    digest = sha256()
    try:
        with temporary.open("wb") as handle:
            first = True
            while chunk := await source.read(1024 * 1024):
                if first and not check_magic(extension, chunk[:8]):
                    raise HTTPException(
                        status_code=415,
                        detail=f"文件内容与扩展名 {extension} 不符，已拒绝",
                    )
                first = False
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件不能超过 {limit // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
                handle.write(chunk)
        if digest.hexdigest() != corpus.source_hash:
            raise HTTPException(
                status_code=409,
                detail=(
                    "上传的内容与这份索引不一致（哈希不匹配）。请选择与原始文件完全相同的副本；"
                    "如果是另一本书，请改用「导入新文件」。"
                ),
            )
        upload_dir = service.config.input_dir / corpus_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / _safe_filename(source.filename, f"source{extension}")
        os.replace(temporary, destination)
        service.import_source(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
        await source.close()
    return RedirectResponse(
        f"/corpora/{corpus_id}/preview?rebound=1", status_code=303
    )


CONFIRMATION_WORD = "删除"


def _confirmation_ok(value: str, corpus: Corpus) -> bool:
    text = (value or "").strip()
    return text in {CONFIRMATION_WORD, "delete", corpus.id, corpus.id[:8]}


@router.get("/corpora/{corpus_id}/delete")
def corpus_delete_form(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        impact = service.corpus_deletion_impact(corpus_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/delete.html",
        {
            "impact": impact,
            "display_name": display_name(impact["corpus"]),
            "confirmation_word": CONFIRMATION_WORD,
            "error": None,
        },
    )


@router.post("/corpora/{corpus_id}/delete")
def corpus_delete(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    confirm: Annotated[str, Form()] = "",
):
    try:
        impact = service.corpus_deletion_impact(corpus_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    context = {
        "impact": impact,
        "display_name": display_name(impact["corpus"]),
        "confirmation_word": CONFIRMATION_WORD,
    }
    if impact["running"] or impact["active_jobs"]:
        return TEMPLATES.TemplateResponse(
            request,
            "corpora/delete.html",
            {
                **context,
                "error": "这份材料还有正在运行的单元或任务。请先到任务页暂停或取消，再删除。",
            },
            status_code=409,
        )
    if not _confirmation_ok(confirm, impact["corpus"]):
        return TEMPLATES.TemplateResponse(
            request,
            "corpora/delete.html",
            {
                **context,
                "error": f"确认文字不匹配，没有删除任何内容。请输入「{CONFIRMATION_WORD}」或该语料 ID 前 8 位。",
            },
            status_code=400,
        )
    service.delete_corpus(corpus_id)
    removed_name = impact["corpus"].name
    return RedirectResponse(
        f"/corpora?deleted={quote(removed_name)}", status_code=303
    )


@router.get("/corpora/{corpus_id}/preview")
def corpus_preview(
    request: Request,
    corpus_id: str,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    rebound: int = 0,
):
    corpus = service.repository.get_corpus(corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    total_chapters = service.repository.count_source_chapters(corpus_id)
    page_count = max(1, math.ceil(total_chapters / page_size))
    page = min(page, page_count)
    chapters = service.repository.list_source_chapters(
        corpus_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    units = service.repository.list_units(corpus_id)
    manifest = service.importer.load_manifest(corpus)
    frozen_reason = _frozen_reason(service, corpus_id, units)
    unit_ordinals: dict[str, list[int]] = {}
    unit_statuses: dict[str, str] = {}
    for unit in units:
        for chapter_id in unit.source_chapter_ids:
            unit_ordinals.setdefault(chapter_id, []).append(unit.ordinal)
            unit_statuses.setdefault(chapter_id, unit.status.value)
    return TEMPLATES.TemplateResponse(
        request,
        "corpora/preview.html",
        {
            "corpus": corpus,
            "display_name": display_name(corpus),
            "card": _corpus_card(service, corpus, units=units, manifest=manifest),
            "chapters": [
                {
                    "chapter": chapter,
                    "paragraph_count": len(chapter.paragraphs),
                    "unit_ordinals": unit_ordinals.get(chapter.id, []),
                    "unit_status": unit_statuses.get(chapter.id),
                }
                for chapter in chapters
            ],
            "unit_ordinals": unit_ordinals,
            "unit_statuses": unit_statuses,
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "total_chapters": total_chapters,
            "first_ordinal": (page - 1) * page_size + 1,
            "last_ordinal": (page - 1) * page_size + len(chapters),
            "unit_count": len(units),
            "frozen_reason": frozen_reason,
            "overrides": _boundary_override_count(service, corpus),
            "confidence": manifest.confidence if manifest else None,
            "diagnostics": manifest.diagnostics if manifest else [],
            "diagnostic_notes": [
                diagnostic_note(code) for code in (manifest.diagnostics if manifest else [])
            ],
            "candidates": (manifest.candidate_chapters[:20] if manifest else []),
            "rebound": bool(rebound),
            "total_characters": manifest.total_characters if manifest else None,
        },
    )


@router.post("/corpora/{corpus_id}/boundaries/edit")
def edit_boundary(
    corpus_id: str,
    action: Annotated[str, Form()],
    chapter_id: Annotated[str, Form()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
    title: Annotated[str | None, Form()] = None,
    paragraph_index: Annotated[int | None, Form()] = None,
    page: Annotated[int, Form(ge=1)] = 1,
):
    """One structured boundary edit from the preview page.

    The JSON endpoint below stays available for scripted use; this one lets the
    preview page offer per-chapter buttons instead of hand written JSON.
    """
    try:
        edit = BoundaryEdit.model_validate(
            {
                "action": action,
                "chapter_id": chapter_id,
                "title": (title or "").strip() or None,
                "paragraph_index": paragraph_index,
            }
        )
        service.apply_boundary_overrides(corpus_id, [edit])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(
        f"/corpora/{corpus_id}/preview?page={page}", status_code=303
    )


RUNNING_STATUSES = (
    UnitStatus.AUTHOR_GENERATING,
    UnitStatus.PASSAGE_REVIEWING,
    UnitStatus.AUTHOR_REVISION_REQUIRED,
    UnitStatus.EXAMINER_GENERATING,
    UnitStatus.VALIDATING,
    UnitStatus.EXAMINER_REVISION_REQUIRED,
)


def _safe_filename(name: str | None, fallback: str) -> str:
    """Keep the original file name for a forgiving artifact directory slug."""
    candidate = (name or "").replace("\\", "/").split("/")[-1].strip().lstrip(".")
    return candidate or fallback


def display_name(corpus: Corpus) -> str:
    """Readable label for a corpus without rewriting the stored file name."""
    stem = Path(corpus.source_path).stem or corpus.name
    cleaned = METADATA_JUNK.sub(" ", stem.replace(".", " ")).replace("(", " ").replace(")", " ")
    cleaned = SEPARATORS.sub(" ", cleaned).strip(" -_·~")
    return cleaned or corpus.name


def diagnostic_note(code: str) -> str:
    """Turn a parser diagnostic code into a sentence someone can act on."""
    if code in DIAGNOSTIC_LABELS:
        return DIAGNOSTIC_LABELS[code]
    name, _, value = code.partition(":")
    label = DIAGNOSTIC_PREFIX_LABELS.get(name)
    if label:
        text = f"{label} {value}"
        if name == "large_preamble":
            text = f"前言占正文的 {value}"
        return text
    return code


def _next_step(
    unit_count: int,
    completed: int,
    approval: dict[str, Any] | None,
    needs_review: int,
) -> dict[str, Any]:
    if unit_count == 0:
        return {
            "key": "calibrate",
            "label": "需要校准边界",
            "hint": "没有可靠章节，先在解析页人工修正，或换一份更规整的源文件。",
            "href": "preview",
            "action": "去校准边界",
        }
    if needs_review and not completed:
        return {
            "key": "review",
            "label": "有待复核单元",
            "hint": f"{needs_review} 个单元达到返工上限，先看报告再决定是否重跑。",
            "href": "configure",
            "action": "查看生成配置",
        }
    if approval is None:
        return {
            "key": "configure",
            "label": "待生成样篇",
            "hint": "先产出一篇样篇并人工批准，之后才允许批量生成。",
            "href": "configure",
            "action": "配置与生成",
        }
    if completed < unit_count:
        return {
            "key": "generate",
            "label": "生成中",
            "hint": f"样篇已批准，已完成 {completed} / {unit_count} 个单元。",
            "href": "configure",
            "action": "继续生成",
        }
    return {
        "key": "practice",
        "label": "可以练习",
        "hint": f"{completed} 篇全部完成，去练习中心做题并看解析。",
        "href": "practice",
        "action": "进入练习中心",
    }


def _corpus_card(
    service: ReadingStudioService,
    corpus: Corpus,
    *,
    units: list[GenerationUnit] | None = None,
    manifest: Any | None = None,
) -> dict[str, Any]:
    if units is None:
        units = service.repository.list_units(corpus.id)
    statuses = dict(Counter(unit.status.value for unit in units))
    if manifest is None:
        manifest = service.importer.load_manifest(corpus)
    jobs = service.repository.list_jobs(corpus.id)
    approval = service.repository.get_latest_corpus_approval(corpus.id)
    unit_count = len(units)
    completed = statuses.get(UnitStatus.COMPLETED.value, 0)
    needs_review = statuses.get(UnitStatus.NEEDS_REVIEW.value, 0)
    failed = statuses.get(UnitStatus.FAILED.value, 0)
    running = sum(statuses.get(status.value, 0) for status in RUNNING_STATUSES)
    confidence = manifest.confidence if manifest else None
    diagnostics = list(manifest.diagnostics) if manifest else []
    confidence_known = MANIFEST_REBUILT_DIAGNOSTIC not in diagnostics
    return {
        "corpus": corpus,
        "display_name": display_name(corpus),
        "source_name": Path(corpus.source_path).name,
        "source_exists": Path(corpus.source_path).exists(),
        "imported_at": corpus.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
        "statuses": statuses,
        "unit_count": unit_count,
        "completed": completed,
        "needs_review": needs_review,
        "failed": failed,
        "running": running,
        "queued": statuses.get(UnitStatus.INDEXED.value, 0),
        "practice_ready": completed,
        "progress": round(completed / unit_count * 100) if unit_count else 0,
        "approved": approval is not None,
        "approval_unit": (approval or {}).get("unit_id"),
        "job_count": len(jobs),
        "last_job": jobs[0] if jobs else None,
        # 材料卡只说"最近任务 succeeded"没人看得懂，也给不出回任务页的入口。
        "last_job_label": job_status_label(jobs[0]["status"]) if jobs else None,
        "confidence": confidence,
        "confidence_percent": (
            round(confidence * 100) if confidence is not None and confidence_known else None
        ),
        "low_confidence": (
            confidence_known and confidence is not None and confidence < 0.6
        ),
        "total_characters": manifest.total_characters if manifest else None,
        "diagnostics": diagnostics,
        "diagnostic_notes": [diagnostic_note(code) for code in diagnostics],
        "step": _next_step(unit_count, completed, approval, needs_review),
    }


def _frozen_reason(
    service: ReadingStudioService,
    corpus_id: str,
    units: list[GenerationUnit],
) -> str | None:
    if any(unit.status != UnitStatus.INDEXED for unit in units):
        return "已有单元进入生成流程"
    if service.repository.list_jobs(corpus_id):
        return "该语料已创建过生成任务"
    if service.repository.get_latest_corpus_approval(corpus_id):
        return "样篇已被批准"
    if service.repository.corpus_has_stage_attempts(corpus_id):
        return "已存在阶段调用记录"
    return None


def _boundary_override_count(service: ReadingStudioService, corpus: Corpus) -> int:
    """Records kept for the most recent boundary change (the file is rewritten)."""
    manifest = service.importer.locate_manifest(corpus)
    if manifest is None:
        return 0
    path = manifest.parent / "boundary-overrides.json"
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    edits = payload.get("edits") if isinstance(payload, dict) else None
    return len(edits) if isinstance(edits, list) else 0


@router.post("/corpora/{corpus_id}/boundaries")
def save_boundary_overrides(
    corpus_id: str,
    overrides: Annotated[str, Form()],
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    try:
        raw_edits = json.loads(overrides)
        if not isinstance(raw_edits, list):
            raise TypeError("Boundary overrides must be a JSON list")
        edits = [BoundaryEdit.model_validate(value) for value in raw_edits]
        service.apply_boundary_overrides(corpus_id, edits)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/corpora/{corpus_id}/preview", status_code=303)
