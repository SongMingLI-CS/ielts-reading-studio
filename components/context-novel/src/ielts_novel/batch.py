from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ielts_novel.config import AppConfig
from ielts_novel.models import Chapter, VocabularyItem
from ielts_novel.orchestrator import RunLimitError, build_volume, process_single_chapter
from ielts_novel.providers.base import ModelProvider


@dataclass
class BatchSummary:
    requested: int = 0
    completed: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    inserted_total: int = 0
    aborted: str | None = None

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "completed": self.completed,
            "failed": self.failed,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "inserted_total": self.inserted_total,
            "aborted": self.aborted,
        }


def format_status(*, total: int, done: int, success: int, failed: int, current: int | None, elapsed: float, inserted: int) -> str:
    rate = elapsed / done if done else 0.0
    remaining = max(total - done, 0) * rate
    return (
        f"总章节数 {total} | 已完成 {done} | 当前章节 {current if current is not None else '-'} | "
        f"成功 {success} | 失败 {failed} | 已用 {elapsed / 60:.1f} 分钟 | "
        f"预计剩余 {remaining / 60:.1f} 分钟 | 词汇插入 {inserted}"
    )


class BatchRunner:
    """Chapter-by-chapter runner with checkpointing, retries, reports and volume merging."""

    def __init__(
        self,
        config: AppConfig,
        chapters: list[Chapter],
        catalog: list[VocabularyItem],
        provider: ModelProvider,
        *,
        protected_terms: list[str] | None = None,
        log=print,
        clock=time.monotonic,
        max_workers: int | None = None,
    ):
        self.config = config
        self.chapters = chapters
        self.catalog = catalog
        self.provider = provider
        self.protected_terms = protected_terms
        self.log = log
        self.clock = clock
        self.max_workers = max_workers or config.concurrency
        self.output = config.output_dir
        self._lock = threading.Lock()

    def _process(self, chapter_id: int):
        return process_single_chapter(self.chapters[chapter_id - 1], self.catalog, self.provider, self.config, protected_terms=self.protected_terms)

    def run(self, chapter_ids: list[int]) -> BatchSummary:
        """Serial default path: one chapter at a time, saving immediately after each one."""
        summary = BatchSummary(requested=len(chapter_ids))
        started = self.clock()
        success = done = consecutive_failures = 0
        for chapter_id in chapter_ids:
            if consecutive_failures >= self.config.max_consecutive_failures:
                summary.aborted = f"连续 {consecutive_failures} 章失败，已停止批处理"
                self.log(summary.aborted)
                break
            try:
                report = self._process(chapter_id)
                success += 1
                consecutive_failures = 0
                summary.completed.append(chapter_id)
                summary.inserted_total += int(report.metrics.get("inserted_count", 0))
            except RunLimitError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed chapter must not stop the batch
                consecutive_failures += 1
                summary.failed[chapter_id] = getattr(exc, "error_type", type(exc).__name__)
                self.log(f"第 {chapter_id} 章失败：{summary.failed[chapter_id]}（连续失败 {consecutive_failures}）")
            done += 1
            self.log(format_status(total=len(chapter_ids), done=done, success=success, failed=len(summary.failed), current=chapter_id, elapsed=self.clock() - started, inserted=summary.inserted_total))
            if done % self.config.check_report_every == 0:
                self._write_check_report(summary, done, started)
            if done % self.config.volume_size == 0:
                self._merge_volume(chapter_id)
        summary.elapsed_seconds = self.clock() - started
        self._write_check_report(summary, done, started, final=True)
        return summary

    def run_concurrent(self, chapter_ids: list[int]) -> BatchSummary:
        """Same policy as :meth:`run` but with the configured number of parallel workers.

        A supervisor loop keeps at most ``workers`` chapters in flight so the consecutive-failure
        stop condition can still cancel the remaining backlog.
        """
        summary = BatchSummary(requested=len(chapter_ids))
        started = self.clock()
        workers = max(1, min(self.max_workers, len(chapter_ids)))
        pending = list(chapter_ids)
        done = success = consecutive_failures = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict = {}

            def fill() -> None:
                while pending and len(futures) < workers:
                    chapter_id = pending.pop(0)
                    futures[pool.submit(self._process, chapter_id)] = chapter_id

            fill()
            while futures:
                future = next(iter(as_completed(list(futures))))
                chapter_id = futures.pop(future)
                done += 1
                try:
                    report = future.result()
                    success += 1
                    consecutive_failures = 0
                    summary.completed.append(chapter_id)
                    summary.inserted_total += int(report.metrics.get("inserted_count", 0))
                except RunLimitError:
                    for running in futures:
                        running.cancel()
                    raise
                except Exception as exc:  # noqa: BLE001 - keep going, record the failure
                    consecutive_failures += 1
                    summary.failed[chapter_id] = getattr(exc, "error_type", type(exc).__name__)
                    self.log(f"第 {chapter_id} 章失败：{summary.failed[chapter_id]}（连续失败 {consecutive_failures}）")
                self.log(format_status(total=len(chapter_ids), done=done, success=success, failed=len(summary.failed), current=chapter_id, elapsed=self.clock() - started, inserted=summary.inserted_total))
                if done % self.config.check_report_every == 0:
                    self._write_check_report(summary, done, started)
                if consecutive_failures >= self.config.max_consecutive_failures:
                    summary.aborted = f"连续 {consecutive_failures} 章失败，已停止批处理"
                    self.log(summary.aborted)
                    for running in futures:
                        running.cancel()
                    break
                fill()
        summary.elapsed_seconds = self.clock() - started
        self._write_check_report(summary, done, started, final=True)
        for chapter_id in sorted(summary.completed):
            if chapter_id % self.config.volume_size == 0:
                self._merge_volume(chapter_id)
        return summary

    def _write_check_report(self, summary: BatchSummary, done: int, started: float, *, final: bool = False) -> Path:
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "chapters_processed": done,
            "success": len(summary.completed),
            "failed": len(summary.failed),
            "failed_chapters": summary.failed,
            "inserted_total": summary.inserted_total,
            "elapsed_seconds": round(self.clock() - started, 1),
            "final": final,
        }
        target = self.output / "reports" / "check_reports" / f"check_{done:05d}{'_final' if final else ''}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _merge_volume(self, chapter_id: int) -> None:
        size = self.config.volume_size
        start = chapter_id - size + 1
        volume_index = (chapter_id + size - 1) // size
        try:
            path = build_volume(self.output, start=start, end=chapter_id, volume_index=volume_index)
        except Exception as exc:  # noqa: BLE001 - volume merging must never break the run
            self.log(f"第 {chapter_id} 章分卷合并失败：{type(exc).__name__}")
            return
        if path:
            self.log(f"已生成分卷：{path.name}")
