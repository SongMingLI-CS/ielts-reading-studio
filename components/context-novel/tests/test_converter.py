from __future__ import annotations

import json
import string

from ielts_novel.models import Chapter, ConvertedChapter, ConvertedParagraph, Paragraph
from ielts_novel.processors.chapter_converter import (
    ChapterConversionError,
    ChapterConverter,
)
from ielts_novel.providers.base import ProviderResult


class SequenceProvider:
    def __init__(self, chapters):
        self.chapters = list(chapters)
        self.models = []
        self.retry_notes = []
        self.input_chapters = []

    def generate_chapter(self, chapter, target, review, **kwargs):
        self.models.append(kwargs.get("model", "flash"))
        self.retry_notes.append(kwargs.get("retry_note"))
        self.input_chapters.append(chapter)
        value = self.chapters.pop(0)
        return ProviderResult(value, value.model_dump_json(), "flash", 10, 20, 0, 0.1)

    def retry_chapter(self, chapter, target, review, **kwargs):
        self.models.append(kwargs.get("model", "pro"))
        self.retry_notes.append(kwargs.get("retry_note"))
        self.input_chapters.append(chapter)
        value = self.chapters.pop(0)
        return ProviderResult(value, value.model_dump_json(), "pro", 10, 20, 0, 0.1)


def _source():
    return Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="韩立向前走。" * 100)])


def _word(index: int) -> str:
    return "word" + string.ascii_lowercase[index // 26] + string.ascii_lowercase[index % 26]


def _output(count):
    terms = [{"word": _word(i), "lemma": _word(i), "meaning": "含义", "part_of_speech": "noun", "cefr": "B2"} for i in range(count)]
    text = "".join(f"韩立向前走{' ' + _word(i) + '（含义）' if i < count else ''}。" for i in range(100))
    return ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])


def test_flash_quality_failure_retries_once_then_upgrades_to_pro(tmp_path):
    provider = SequenceProvider([_output(2), _output(3), _output(28)])
    converter = ChapterConverter(provider, tmp_path, max_quality_attempts=3)
    result, report, usage = converter.convert(_source(), [], [], protected_terms=["韩立"])
    assert report.passed
    assert result.paragraphs
    assert [entry.model for entry in usage] == ["flash", "flash", "pro"]
    assert "density_too_low" in provider.retry_notes[1]
    assert "实际插入 2" in provider.retry_notes[1]
    assert "wordaa（含义）" in provider.input_chapters[1].paragraphs[0].text
    assert len(list((tmp_path / "raw_responses").glob("*.json"))) == 3
    metadata = json.loads((tmp_path / "raw_responses" / "chapter_0001_attempt_1.json").read_text(encoding="utf-8"))
    assert metadata["input_tokens"] == 10 and metadata["output_tokens"] == 20
    assert metadata["status"] == "quality_failed"


def test_still_invalid_pro_result_is_saved_as_failed(tmp_path):
    provider = SequenceProvider([_output(1), _output(1), _output(1)])
    converter = ChapterConverter(provider, tmp_path, max_quality_attempts=3)
    try:
        converter.convert(_source(), [], [])
    except ChapterConversionError:
        pass
    assert (tmp_path / "failed" / "chapter_0001.json").exists()
