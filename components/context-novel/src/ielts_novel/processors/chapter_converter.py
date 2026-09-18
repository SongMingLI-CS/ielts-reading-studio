from __future__ import annotations

import json
from pathlib import Path

from ielts_novel.models import (
    Chapter,
    ConvertedChapter,
    InsertedTerm,
    Paragraph,
    UsageRecord,
    VocabularyItem,
)
from ielts_novel.processors.quality_checker import QualityChecker, QualityReport
from ielts_novel.processors.term_extractor import (
    build_lookup,
    normalize_chapter,
    repair_chapter_stacking,
)
from ielts_novel.providers.base import ModelProvider, ProviderResult


class ChapterConversionError(RuntimeError):
    pass


class ChapterConverter:
    def __init__(
        self,
        provider: ModelProvider,
        output_dir: str | Path,
        *,
        max_quality_attempts: int = 3,
        checker: QualityChecker | None = None,
        raw_name_prefix: str | None = None,
        initial_note: str | None = None,
        catalog: list[InsertedTerm] | None = None,
    ):
        self.provider = provider
        self.output_dir = Path(output_dir)
        self.max_quality_attempts = max_quality_attempts
        self.checker = checker or QualityChecker()
        self.raw_name_prefix = raw_name_prefix
        self.initial_note = initial_note
        self.lookup = build_lookup(catalog or [])

    def convert(
        self,
        chapter: Chapter,
        target: list[VocabularyItem],
        review: list[VocabularyItem],
        *,
        protected_terms: list[str] | None = None,
        known_terms: dict[str, InsertedTerm] | None = None,
    ) -> tuple[ConvertedChapter, QualityReport, list[UsageRecord]]:
        usage: list[UsageRecord] = []
        last_result: ProviderResult | None = None
        last_report: QualityReport | None = None
        retry_note = self.initial_note or "首次生成"
        working_chapter = chapter
        lookup = dict(self.lookup)
        raw_dir = self.output_dir / "raw_responses"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.max_quality_attempts):
            result = (
                self.provider.generate_chapter(working_chapter, target, review, retry_note=retry_note)
                if attempt < 2
                else self.provider.retry_chapter(working_chapter, target, review, retry_note="Flash 两次质量检查失败；" + retry_note)
            )
            # The converted text is the source of truth: rebuild terms from it so declared but
            # unwritten items can never inflate density, then enforce the per-sentence cap.
            normalized = repair_chapter_stacking(normalize_chapter(result.chapter, lookup=lookup), lookup=lookup)
            if normalized != result.chapter:
                result = ProviderResult(normalized, result.raw_content, result.model, result.input_tokens, result.output_tokens, result.retry_count, result.elapsed_seconds)
            last_result = result
            report = self.checker.check(chapter, result.chapter, protected_terms=protected_terms, lookup=known_terms)
            last_report = report
            retry_note = f"质量问题：{','.join(report.issues)}；实际插入 {report.metrics.get('inserted_count', 0)} 项，密度 {report.metrics.get('density_per_500', 0)}/500。必须逐段达到输入 JSON 的 minimum_items"

            if report.issues == ["density_too_low"]:
                working_chapter = chapter.model_copy(update={"paragraphs": [Paragraph(id=item.id, text=item.converted_text) for item in result.chapter.paragraphs]})
            prefix = self.raw_name_prefix or f"chapter_{chapter.chapter_id:04d}"
            raw_path = raw_dir / f"{prefix}_attempt_{attempt + 1}.json"
            version = 2
            while raw_path.exists():
                raw_path = raw_dir / f"{prefix}_attempt_{attempt + 1}_run{version}.json"
                version += 1
            raw_path.write_text(json.dumps({
                "chapter_id": chapter.chapter_id,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "retry_count": result.retry_count,
                "elapsed_seconds": result.elapsed_seconds,
                "status": "success" if report.passed else "quality_failed",
                "error_type": None if report.passed else ",".join(report.issues),
                "raw_content": result.raw_content,
                "quality": report.__dict__,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            usage.append(UsageRecord(chapter_id=chapter.chapter_id, model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens, retry_count=result.retry_count, status="success" if report.passed else "quality_failed", error_type=None if report.passed else ",".join(report.issues)))
            if report.passed:
                return result.chapter, report, usage
        failed_dir = self.output_dir / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.raw_name_prefix or f"chapter_{chapter.chapter_id:04d}"
        failed_path = failed_dir / f"{prefix}.json"
        version = 2
        while failed_path.exists():
            failed_path = failed_dir / f"{prefix}_run{version}.json"
            version += 1
        failed_path.write_text(json.dumps({"source": chapter.model_dump(mode="json"), "last_response": last_result.raw_content if last_result else None, "quality": last_report.__dict__ if last_report else None}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise ChapterConversionError(f"chapter {chapter.chapter_id} failed quality checks")
