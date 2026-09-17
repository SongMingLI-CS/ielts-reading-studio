from __future__ import annotations

import json

from ielts_novel.batch import BatchRunner, format_status
from ielts_novel.config import AppConfig
from ielts_novel.models import Chapter, ConvertedChapter, ConvertedParagraph, InsertedTerm, Paragraph, VocabularyItem
from ielts_novel.providers.base import ProviderResult


class StubProvider:
    """Returns a passing conversion for every chapter except the ids listed as failing."""

    def __init__(self, *, failing: set[int] | None = None):
        self.failing = failing or set()
        self.calls: list[int] = []

    def _chapter(self, chapter: Chapter) -> ConvertedChapter:
        if chapter.chapter_id in self.failing:
            raise RuntimeError("章节质量检查失败")
        words = [f"word{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(28)]
        terms = [InsertedTerm(word=w, lemma=w, meaning="含义", part_of_speech="noun", cefr="B2") for w in words]
        text = "".join(f"韩立缓慢走向山边小村{' ' + words[i] + '（含义）' if i < len(words) else ''}。" for i in range(50))
        return ConvertedChapter(chapter_id=chapter.chapter_id, chapter_title=chapter.chapter_title, paragraphs=[ConvertedParagraph(id=chapter.paragraphs[0].id, converted_text=text, inserted_terms=terms)])

    def generate_chapter(self, chapter, target, review, **kwargs):
        self.calls.append(chapter.chapter_id)
        converted = self._chapter(chapter)
        return ProviderResult(converted, converted.model_dump_json(), "deepseek-flash", 100, 200, 0, 0.1)

    def retry_chapter(self, chapter, target, review, **kwargs):
        return self.generate_chapter(chapter, target, review, **kwargs)


def _chapters(count: int) -> list[Chapter]:
    return [Chapter(chapter_id=index, chapter_title=f"第{index:04d}章", paragraphs=[Paragraph(id=f"{index}-001", text="韩立缓慢走向山边小村。" * 50)]) for index in range(1, count + 1)]


def _config(tmp_path, **overrides) -> AppConfig:
    defaults = dict(output_dir=tmp_path / "output", batch_confirmed=True, max_chapters_per_run=50, concurrency=1, check_report_every=20, volume_size=50)
    defaults.update(overrides)
    return AppConfig(**defaults)


def _catalog() -> list[VocabularyItem]:
    return [VocabularyItem(word=f"term{i}", lemma=f"term{i}", meaning="义", part_of_speech="noun", cefr="B2") for i in range(40)]


def test_serial_batch_processes_range_and_skips_completed(tmp_path):
    config = _config(tmp_path)
    runner = BatchRunner(config, _chapters(3), _catalog(), StubProvider(), log=lambda message: None)

    summary = runner.run([1, 2])

    assert summary.completed == [1, 2]
    assert not summary.failed
    assert (config.output_dir / "chapter_json" / "第0001章.json").exists()
    assert (config.output_dir / "chapters" / "第0002章.docx").exists()
    progress = json.loads((config.output_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["completed"] == [1, 2]


def test_batch_records_failure_and_continues_with_next_chapter(tmp_path):
    config = _config(tmp_path)
    provider = StubProvider(failing={2})
    runner = BatchRunner(config, _chapters(3), _catalog(), provider, log=lambda message: None)

    summary = runner.run([1, 2, 3])

    assert summary.completed == [1, 3]
    assert list(summary.failed) == [2]
    assert provider.calls == [1, 2, 3]


def test_batch_stops_after_consecutive_failures(tmp_path):
    config = _config(tmp_path, max_consecutive_failures=2)
    provider = StubProvider(failing={2, 3, 4, 5})
    runner = BatchRunner(config, _chapters(6), _catalog(), provider, log=lambda message: None)

    summary = runner.run([1, 2, 3, 4, 5, 6])

    assert summary.completed == [1]
    assert list(summary.failed) == [2, 3]
    assert summary.aborted is not None
    assert 6 not in provider.calls


def test_batch_writes_check_report_and_volume(tmp_path):
    config = _config(tmp_path, check_report_every=2, volume_size=2)
    runner = BatchRunner(config, _chapters(4), _catalog(), StubProvider(), log=lambda message: None)

    summary = runner.run([1, 2, 3, 4])

    assert summary.completed == [1, 2, 3, 4]
    reports = sorted((config.output_dir / "reports" / "check_reports").glob("check_*.json"))
    assert reports
    assert (config.output_dir / "volumes" / "第01卷_第1至2章.docx").exists()
    assert (config.output_dir / "volumes" / "第02卷_第3至4章.docx").exists()


def test_concurrent_batch_uses_workers_and_keeps_results(tmp_path):
    config = _config(tmp_path, concurrency=3)
    runner = BatchRunner(config, _chapters(5), _catalog(), StubProvider(), log=lambda message: None, max_workers=3)

    summary = runner.run_concurrent([1, 2, 3, 4, 5])

    assert sorted(summary.completed) == [1, 2, 3, 4, 5]
    assert not summary.failed


def test_status_line_reports_all_required_counters():
    line = format_status(total=100, done=20, success=19, failed=1, current=21, elapsed=600, inserted=1800)
    for fragment in ["总章节数 100", "已完成 20", "当前章节 21", "成功 19", "失败 1", "已用 10.0 分钟", "预计剩余", "词汇插入 1800"]:
        assert fragment in line
