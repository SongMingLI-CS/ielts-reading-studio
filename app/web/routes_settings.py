from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.agents.base import ModelRequest, ProviderError
from app.agents.deepseek import DeepSeekProvider
from app.pipeline.service import ReadingStudioService

from .dependencies import get_service

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parents[2] / "templates")


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _path_size(path: Path) -> dict[str, Any]:
    exists = path.exists()
    if path.is_file():
        size = path.stat().st_size
    elif path.is_dir():
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    else:
        size = 0
    return {
        "path": str(path),
        "exists": exists,
        "size": _human_bytes(size) if exists else "—",
    }


def _secret_state(config: Any) -> list[dict[str, str]]:
    key = config.deepseek_api_key
    has_env = bool(os.getenv("DEEPSEEK_API_KEY"))
    sources: list[str] = []
    if has_env:
        sources.append("进程环境变量 DEEPSEEK_API_KEY（服务启动时注入）")
    for filename, label in (
        (".env", ".env 文件"),
        (".env.web", "服务器 .env.web（systemd 注入）"),
    ):
        candidate = config.base_dir / filename
        if candidate.is_file() and "DEEPSEEK_API_KEY" in candidate.read_text(
            encoding="utf-8", errors="ignore"
        ):
            sources.append(f"{candidate}（{label}）")
    configured = key is not None or has_env
    return [
        {
            "name": "DeepSeek API Key",
            "state": "已配置" if configured else "未配置",
            "detail": "来自 " + "、".join(sources)
            if sources
            else "未在任何位置找到，生成题目会直接失败",
            "ok": configured,
        },
        {
            "name": "网页登录口令",
            "state": "已启用" if config.web_username else "未启用（任何人可访问）",
            "detail": f"用户名 {config.web_username}"
            if config.web_username
            else "设置 IELTS_WEB_USERNAME / IELTS_WEB_PASSWORD 后生效",
            "ok": bool(config.web_username),
        },
    ]


def _dir_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _settings_groups(service: ReadingStudioService) -> list[dict[str, Any]]:
    config = service.config
    library = config.database_path.parent
    return [
        {
            "title": "模型与生成",
            "blurb": "决定文章和题目由谁生成、每次跑多少。",
            "rows": [
                {
                    "name": "出题模型",
                    "value": config.author_model,
                    "note": "负责写文章和题目。想更强或更便宜就换这里。",
                },
                {
                    "name": "审校模型",
                    "value": config.examiner_model,
                    "note": "负责质检、判分口径和返工建议。",
                },
                {
                    "name": "API 地址",
                    "value": config.deepseek_base_url,
                    "note": "OpenAI 兼容端点，换供应商时改这里。",
                },
                {
                    "name": "默认 单批篇数 / 并发",
                    "value": f"{config.batch_size} 篇 · {config.concurrency} 并发",
                    "note": "批量生成时的默认值，单次任务可在「生成题目」页临时覆盖。",
                },
                {
                    "name": "返工上限",
                    "value": (
                        f"出题侧 {config.author_revision_limit} 轮 · "
                        f"审校侧 {config.examiner_revision_limit} 轮"
                    ),
                    "note": "质检不通过时最多重写几次，超过就转为「待审阅」交人工。",
                },
                {
                    "name": "单次上限",
                    "value": (
                        f"{config.max_units_per_run} 篇 · "
                        f"{config.max_estimated_tokens_per_run:,} tokens · "
                        f"连续失败 {config.max_consecutive_failures} 次即停"
                    ),
                    "note": "刹车片，防止一次点错消耗太多额度。",
                },
            ],
        },
        {
            "title": "质检与审阅",
            "blurb": "决定生成时拦下什么、以及需要多少人看。",
            "rows": [
                {
                    "name": "题目重复门禁",
                    "value": f"相似度 ≥ {config.question_duplicate_threshold:.0%}",
                    "note": "新题与已有题目超过这个相似度就判为重复，进入返工；改不动就转人工。",
                },
                {
                    "name": "相似度审阅页阈值",
                    "value": f"≥ {config.question_report_threshold:.0%}",
                    "note": "审阅页列出疑似重复的宽松阈值，宁可多列给人看，不在生成时误拦。",
                },
                {
                    "name": "批任务自动抽样",
                    "value": (
                        f"{config.review_sample_rate:.0%} · 至少 {config.review_sample_min} 篇"
                    ),
                    "note": "一批跑完后自动抽几篇进「抽样审阅」队列，由人读一遍再决定通过或返工。",
                },
            ],
        },
        {
            "title": "切分与合并",
            "blurb": "决定语料库怎么被切成一篇篇练习。",
            "rows": [
                {
                    "name": "最小可用长度",
                    "value": f"{config.min_source_chars:,} 字符",
                    "note": "短于这个长度的语料库不会参与出题。",
                },
                {
                    "name": "自动切分长度",
                    "value": (
                        f"超过 {config.split_source_chars:,} 字符开始切，"
                        f"每篇 {config.split_min_chars:,}–{config.split_max_chars:,}"
                    ),
                    "note": "长语料库按段落就近切分，保证每篇长度适合雅思阅读。",
                },
                {
                    "name": "短章合并",
                    "value": (
                        f"最多 {config.max_merged_chapters} 章 · "
                        f"{config.max_merged_chars:,} 字符封顶"
                    ),
                    "note": "很碎的短章节会被合并成一篇，避免出现超短练习。",
                },
            ],
        },
        {
            "title": "文件位置",
            "blurb": "数据都在这些目录里，备份页可以一键打包。",
            "rows": [
                {
                    "name": "配置文件",
                    "value": str(config.base_dir / "config.yaml"),
                    "note": "非敏感设置都在这个 YAML 里；改完重启服务生效。",
                },
                {
                    "name": "语料源文件",
                    "value": str(config.input_dir),
                    "note": "导入的原文都存在这里。",
                },
                {
                    "name": "数据库",
                    "value": str(config.database_path),
                    "note": "单元、练习记录、生词本都存在这个 SQLite 文件里。",
                },
                {
                    "name": "生成产物",
                    "value": str(config.output_dir),
                    "note": "Package、导出文件、备份归档的根目录。",
                },
                {
                    "name": "资料库体积",
                    "value": (
                        _human_bytes(_dir_bytes(library)) if library.exists() else "—"
                    ),
                    "note": f"统计目录 {library}",
                },
            ],
        },
    ]


def _probe_provider(service: ReadingStudioService) -> dict[str, Any]:
    config = service.config
    if config.deepseek_api_key is None:
        return {
            "ok": False,
            "status": "未配置密钥",
            "detail": "设置 DEEPSEEK_API_KEY（环境变量或 .env）后即可测试。",
            "elapsed": None,
        }
    request = ModelRequest(
        stage="connectivity_probe",
        model=config.author_model,
        system="You are a connectivity probe. Answer with JSON only.",
        user='Reply exactly as {"ok": true}.',
        max_tokens=64,
        temperature=0,
    )
    started = time.monotonic()
    try:
        provider = DeepSeekProvider(config, max_retries=1)
        result = provider.complete_json(request)
    except (ProviderError, ImportError, OSError, ValueError) as exc:
        # Every provider and transport failure is reported on the page instead of
        # bubbling up as a 500, which is the whole point of this probe.
        return {
            "ok": False,
            "status": "连接失败",
            "detail": _redact(service, f"{type(exc).__name__}: {exc}"),
            "elapsed": round(time.monotonic() - started, 2),
        }
    return {
        "ok": True,
        "status": f"{config.author_model} 应答正常",
        "detail": (
            f"返回 {_short(result.payload)} · "
            f"用量 {result.input_tokens}+{result.output_tokens} tokens"
        ),
        "elapsed": round(time.monotonic() - started, 2),
    }


def _short(payload: dict[str, Any], limit: int = 120) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - exotic payload types
        text = str(payload)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"


def _redact(service: ReadingStudioService, text: str) -> str:
    for secret_field in ("deepseek_api_key", "web_password"):
        secret = getattr(service.config, secret_field, None)
        if secret is None:
            continue
        value = secret.get_secret_value()
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


@router.get("/settings")
def settings_page(
    request: Request,
    service: Annotated[ReadingStudioService, Depends(get_service)],
    probed: str | None = None,
    ok: str | None = None,
    status: str | None = None,
    detail: str | None = None,
    elapsed: str | None = None,
):
    probe = None
    if probed:
        probe = {
            "ok": ok == "1",
            "status": status or "已测试",
            "detail": detail or "",
            "elapsed": elapsed,
        }
    return TEMPLATES.TemplateResponse(
        request,
        "settings/index.html",
        {
            "groups": _settings_groups(service),
            "secrets": _secret_state(service.config),
            "probe": probe,
            "storage": [
                {"label": "语料源文件", **_path_size(service.config.input_dir)},
                {"label": "数据库", **_path_size(service.config.database_path)},
                {"label": "生成产物", **_path_size(service.config.output_dir)},
            ],
        },
    )


@router.post("/settings/test-provider")
def test_provider(
    service: Annotated[ReadingStudioService, Depends(get_service)],
):
    result = _probe_provider(service)
    query = urlencode(
        {
            "probed": "1",
            "ok": "1" if result["ok"] else "0",
            "status": result["status"],
            "detail": result["detail"],
            "elapsed": "" if result["elapsed"] is None else str(result["elapsed"]),
        }
    )
    return RedirectResponse(f"/settings?{query}", status_code=303)
