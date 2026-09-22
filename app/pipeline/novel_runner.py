"""Run one context-novel batch as a queued job.

The component keeps its own progress store, so the worker only has to launch its CLI and
record the outcome; the component's existing resume logic does the rest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.pipeline.service import ReadingStudioService

#: The component is vendored in this repository and keeps its own CLI entry point.
COMPONENT_ROOT = Path(__file__).resolve().parents[2] / "components" / "context-novel"


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_system_corpus(service: ReadingStudioService) -> str:
    """Return the id of the system corpus used by non-reading jobs, creating it once."""

    from app.models import Corpus
    from app.storage.repositories import SYSTEM_CORPUS_ID

    if service.repository.get_corpus(SYSTEM_CORPUS_ID) is None:
        service.repository.add_corpus(
            Corpus(
                id=SYSTEM_CORPUS_ID,
                name="情境小说组件（系统）",
                source_path="context-novel",
                source_hash="system-context-novel",
                format="component",
                chapter_count=0,
                parser_version="n/a",
            )
        )
    return SYSTEM_CORPUS_ID


#: 组件报出的失败关键词，从具体到宽泛排列：命中第一个即用（`authentication` 必须先于 `auth`）。
FAILURE_KEYWORDS = (
    "billing",
    "authentication",
    "rate_limit",
    "density_too_low",
    "invalid_response",
    "empty_response",
    "ChapterConversionError",
    "RunLimitError",
    "auth",
)

#: 关键词 → 给操作者看的中文说法。页面不直接甩 `ChapterConversionError` 这种内部标识。
FAILURE_LABELS = {
    "billing": "模型账户余额或配额不足",
    "authentication": "API 密钥无效或已过期",
    "rate_limit": "被模型侧限流",
    "density_too_low": "词汇密度没达到下限",
    "invalid_response": "模型返回的内容不是合法 JSON",
    "empty_response": "模型返回了空响应",
    "ChapterConversionError": "这一章的正文转换失败",
    "RunLimitError": "超出单次运行的章节上限",
    "auth": "密钥或余额问题",
}

#: 关键词 → 给操作者的下一步建议。
FAILURE_HINTS = {
    "billing": (
        "充值或换一个可用的密钥后点「重试失败章节」：只跑失败的那几章，已完成的章节不会重跑、"
        "也不会重复收费。"
    ),
    "authentication": "检查 .env.web 里的 DEEPSEEK_API_KEY 后重试失败章节。",
    "rate_limit": "等几分钟再点「断点继续」或「重试失败章节」。",
    "density_too_low": (
        "换一章更长的、或把密度下限（每 500 字至少 20 个词条）调低后重试；已成功的章节不受影响。"
    ),
    "invalid_response": "重试这一章通常即可恢复。",
    "empty_response": "重试这一章通常即可恢复。",
    "ChapterConversionError": "换一章，或按章节范围分批重试。",
    "RunLimitError": "按章节范围分批生成即可（样章确认前每次只能生成一章）。",
    "auth": "检查 .env.web 里的 DEEPSEEK_API_KEY 与账户余额。",
}


def _failure_keyword(text: str) -> str | None:
    """在失败文本里找出最具体的那个关键词（拿到的是组件自己的 error_type 或其日志行）。"""

    lowered = text.lower()
    for keyword in FAILURE_KEYWORDS:
        if keyword.lower() in lowered:
            return keyword
    return None


def failure_label(text: str) -> str:
    """失败原因的人话说法；认不出就原样返回组件给的关键词，不编造解释。"""

    keyword = _failure_keyword(text)
    return FAILURE_LABELS.get(keyword, text) if keyword else text


def failure_hint(text: str) -> str:
    keyword = _failure_keyword(text)
    return FAILURE_HINTS.get(keyword, "") if keyword else ""


def _read_back_attempt(log_path: Path) -> dict[str, Any]:
    """从本次运行的日志里读出结果摘要与失败原因。

    组件的 CLI 会把最终汇总以一行 JSON 打出来（requested/completed/failed/…），失败章节则
    写成 ``第 N 章失败：原因`` 或 ``ERROR: …``。进程退出码为 0 并不等于有章节产出，所以
    这里把两者都记下来，页面才能区分“跑完了”和“什么都没生成”。
    """

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError:
        return {}
    summary: dict[str, Any] = {}
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("{") and '"requested"' in stripped:
            try:
                summary = json.loads(stripped)
            except ValueError:
                summary = {}
            break
    completed = summary.get("completed") if isinstance(summary.get("completed"), list) else []
    failed = summary.get("failed") if isinstance(summary.get("failed"), dict) else {}
    reason = ""
    for line in reversed(lines):
        if line.startswith("ERROR:") or "章失败：" in line:
            reason = line.strip()[:400]
            break
    payload: dict[str, Any] = {
        "chapters_completed": len(completed),
        "chapters_failed": len(failed),
        "inserted_total": summary.get("inserted_total"),
        "failure_reason": reason,
        "failure_label": failure_label(reason) if reason else "",
    }
    hint = failure_hint(reason)
    if hint:
        payload["failure_hint"] = hint
    return payload


def run_novel_job(_service: Any, payload: dict[str, Any]) -> int:
    """Execute the component CLI for one queued batch and record the outcome."""

    config_path = Path(payload["config_path"])
    status_path = Path(payload["status_path"])
    description = str(payload.get("description") or "情境小说生成")
    arguments = [str(value) for value in payload.get("arguments", [])]
    _write_status(status_path, {"status": "running", "description": description})
    log_path = status_path.parent / "reports" / "web-generation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ielts_novel.cli",
                *arguments,
                "--config",
                str(config_path),
            ],
            cwd=COMPONENT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    attempt = _read_back_attempt(log_path)
    status = "completed" if result.returncode == 0 else "failed"
    if status == "completed" and not attempt.get("chapters_completed"):
        outcome = "no_chapters"
    else:
        outcome = "ok" if status == "completed" else "failed"
    _write_status(
        status_path,
        {
            "status": status,
            "description": description,
            "return_code": result.returncode,
            "outcome": outcome,
            **attempt,
        },
    )
    if result.returncode != 0:
        # Let the worker record a structured error code instead of a silent no-op.
        raise RuntimeError(f"novel component exited with code {result.returncode}")
    return 0
