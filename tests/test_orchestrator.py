from __future__ import annotations

import json

from ielts_novel.config import AppConfig
from ielts_novel.models import Chapter, ConvertedChapter, ConvertedParagraph, InsertedTerm, Paragraph, VocabularyItem
from ielts_novel.orchestrator import convert_chunk_with_fallback, process_single_chapter, split_chapter
from ielts_novel.processors.quality_checker import QualityChecker
from ielts_novel.providers.base import ProviderResult


class PassingProvider:
    def generate_chapter(self, chapter, target, review, **kwargs):
        words = [f"word{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(28)]
        terms = [InsertedTerm(word=w, lemma=w, meaning="含义", part_of_speech="noun", cefr="B2") for w in words]
        text = "".join(f"韩立缓慢走向山边小村{' ' + words[i] + '（含义）' if i < len(words) else ''}。" for i in range(50))
        converted = ConvertedChapter(chapter_id=chapter.chapter_id, chapter_title=chapter.chapter_title, paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])
        return ProviderResult(converted, converted.model_dump_json(), "deepseek-flash", 100, 200, 0, 1.25)

    def retry_chapter(self, *args, **kwargs):
        raise AssertionError("retry should not be used")


def test_process_single_chapter_saves_all_outputs_and_usage(tmp_path):
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="韩立缓慢走向山边小村。" * 50)])
    catalog = [VocabularyItem(word=f"term{i}", lemma=f"term{i}", meaning="义", part_of_speech="noun", cefr="B2") for i in range(40)]
    config = AppConfig(output_dir=tmp_path / "output", batch_confirmed=False)

    report = process_single_chapter(source, catalog, PassingProvider(), config, protected_terms=["韩立"])

    assert report.passed
    assert (config.output_dir / "chapters" / "第0001章.docx").exists()
    assert (config.output_dir / "html" / "第0001章.html").exists()
    assert (config.output_dir / "glossary.json").exists()
    assert (config.output_dir / "glossary.xlsx").exists()
    assert (config.output_dir / "index.html").exists()
    assert (config.output_dir / "chunk_checkpoints" / "chapter_0001_chunk_01.json").exists()
    usage = json.loads((config.output_dir / "reports" / "usage.json").read_text(encoding="utf-8"))
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 200
    progress = json.loads((config.output_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["completed"] == [1]


def test_split_chapter_preserves_ids_and_order():
    chapter = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id=f"1-{i:03d}", text=str(i)) for i in range(1, 18)])
    chunks = split_chapter(chapter, max_paragraphs=8)
    assert [len(chunk.paragraphs) for chunk in chunks] == [8, 8, 1]
    assert [p.id for chunk in chunks for p in chunk.paragraphs] == [p.id for p in chapter.paragraphs]


def test_trim_chapter_density_restores_excess_items_to_chinese():
    from ielts_novel.orchestrator import trim_chapter_density

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他缓慢地前行。" * 10)])  # 60 CJK chars → cap 4 items
    terms = [InsertedTerm(word=f"term{letter}", lemma=f"term{letter}", meaning="前行", part_of_speech="verb", cefr="B2") for letter in "abcdefg"]
    text = "他 terma（前行） termb（前行） termc（前行） termd（前行） terme（前行） termf（前行） termg（前行）。" + "他缓慢地前行。" * 9
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])

    trimmed = trim_chapter_density(source, converted)

    assert len(trimmed.paragraphs[0].inserted_terms) == 4
    assert "termg" not in trimmed.paragraphs[0].converted_text
    assert trimmed.paragraphs[0].converted_text.count("前行") >= 3


def test_trim_chapter_density_keeps_chapters_already_in_band():
    from ielts_novel.orchestrator import trim_chapter_density

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他缓慢地前行。" * 10)])
    term = InsertedTerm(word="proceed", lemma="proceed", meaning="前行", part_of_speech="verb", cefr="B2")
    text = "他缓慢 proceed（前行）。" + "他缓慢地前行。" * 9
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=[term])])

    assert trim_chapter_density(source, converted) == converted


def test_block_gate_tolerates_under_insertion_so_the_chapter_gate_can_repair_it():
    from ielts_novel.orchestrator import BLOCK_MINIMUM_DENSITY_PER_500

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他缓慢地前行。" * 44)])  # 264 CJK chars
    terms = [InsertedTerm(word=f"term{letter}", lemma=f"term{letter}", meaning="前行", part_of_speech="verb", cefr="B2") for letter in "abc"]
    text = "他 terma（前行） termb（前行） termc（前行）缓慢地前行。" + "他缓慢地前行。" * 43  # 3 items in 264 chars ≈ 5.7/500
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])

    block_report = QualityChecker(minimum_density=BLOCK_MINIMUM_DENSITY_PER_500, allow_discrete_rounding=True).check(source, converted)
    chapter_report = QualityChecker().check(source, converted)

    assert "density_too_low" not in block_report.issues
    assert "density_too_low" in chapter_report.issues


def test_term_glued_to_chinese_is_still_found_in_text():
    term = InsertedTerm(word="give up", lemma="give up", meaning="放弃", part_of_speech="phrase", cefr="B2")
    text = "对方并没有真的give up（放弃）他的企图。" + "他缓慢地前行。" * 40
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=[term])])
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="对方并没有真的放弃他的企图。" + "他缓慢地前行。" * 40)])

    report = QualityChecker(minimum_density=0).check(source, converted)

    assert "term_not_in_text" not in report.issues


def test_deterministic_top_up_never_breaks_the_per_sentence_cap():
    from ielts_novel.orchestrator import deterministic_top_up
    from ielts_novel.processors.term_extractor import extract_occurrences, group_by_sentence, sentence_spans

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他凝视着远方，心中有些紧张。" * 8)])
    terms = [InsertedTerm(word=word, lemma=word, meaning="义", part_of_speech="noun", cefr="B2") for word in ("alpha", "beta", "gamma")]
    text = "他 alpha（义） beta（义） gamma（义）凝视着远方，心中有些紧张。" + "他凝视着远方，心中有些紧张。" * 7
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])
    catalog = [VocabularyItem(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="B2")]

    filled = deterministic_top_up(source, converted, catalog, maximum_items=4)

    assert len(filled.paragraphs[0].inserted_terms) == 4
    occurrences = extract_occurrences(filled.paragraphs[0].converted_text)
    for group in group_by_sentence(occurrences, sentence_spans(filled.paragraphs[0].converted_text)):
        assert len(group) <= 3
    from ielts_novel.orchestrator import deterministic_top_up

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他凝视着远方，心中有些紧张。" * 8)])
    term = InsertedTerm(word="proceed", lemma="proceed", meaning="前行", part_of_speech="verb", cefr="B2")
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="他凝视着远方 proceed（前行），心中有些紧张。" + "他凝视着远方，心中有些紧张。" * 7, inserted_terms=[term])])
    catalog = [
        VocabularyItem(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="B2"),
        VocabularyItem(word="tension", lemma="tension", meaning="紧张", part_of_speech="noun", cefr="B2"),
    ]

    filled = deterministic_top_up(source, converted, catalog, maximum_items=len(converted.paragraphs[0].inserted_terms) + 1)

    text = filled.paragraphs[0].converted_text
    assert "gaze（凝视）" in text or "tension（紧张）" in text
    assert len(filled.paragraphs[0].inserted_terms) == 2
    assert "凝视着远方" not in text.split("gaze（凝视）")[0]
    from ielts_novel.orchestrator import block_candidates
    from ielts_novel.processors.vocabulary_selector import VocabularyPlan

    plan = VocabularyPlan(
        new=tuple(VocabularyItem(word=f"new{i}", lemma=f"new{i}", meaning="义", part_of_speech="noun", cefr="B1" if i % 3 else "C1") for i in range(20)),
        review=tuple(VocabularyItem(word=f"rev{i}", lemma=f"rev{i}", meaning="义", part_of_speech="noun", cefr="B2") for i in range(6)),
    )
    windows = [block_candidates(plan, index) for index in range(6)]

    assert all(len(new) <= 8 and len(review) <= 4 for new, review in windows)
    assert all(sum(1 for item in new if item.cefr == "C1") >= 2 for new, _ in windows)
    assert len({item.word for new, _ in windows for item in new}) >= 20
    assert len({item.word for _, review in windows for item in review}) == 6


class SplitFallbackProvider:
    def __init__(self):
        self.paragraph_counts = []

    def generate_chapter(self, chapter, target, review, **kwargs):
        self.paragraph_counts.append(len(chapter.paragraphs))
        converted_paragraphs = []
        for paragraph in chapter.paragraphs:
            terms = []
            text = paragraph.text
            if len(chapter.paragraphs) == 1:
                words = [f"fallback{chr(97 + i)}" for i in range(4)]
                terms = [InsertedTerm(word=w, lemma=w, meaning="含义", part_of_speech="noun", cefr="B2") for w in words]
                source_sentences = [sentence for sentence in paragraph.text.split("。") if sentence]
                sentences = [sentence + (f" {words[i]}（含义）" if i < len(words) else "") + "。" for i, sentence in enumerate(source_sentences)]
                text = "".join(sentences)
            converted_paragraphs.append(ConvertedParagraph(id=paragraph.id, converted_text=text, inserted_terms=terms))
        converted = ConvertedChapter(chapter_id=chapter.chapter_id, chapter_title=chapter.chapter_title, paragraphs=converted_paragraphs)
        return ProviderResult(converted, converted.model_dump_json(), "deepseek-flash", 10, 20, 0, 0.1)

    def retry_chapter(self, chapter, target, review, **kwargs):
        return self.generate_chapter(chapter, target, review, **kwargs)


def test_failed_multi_paragraph_chunk_is_bisected_until_quality_passes(tmp_path):
    chapter = Chapter(
        chapter_id=1,
        chapter_title="第一章",
        paragraphs=[Paragraph(id=f"1-{i:03d}", text=f"韩立沿着第{i}条山路缓慢前行。" * 8) for i in range(1, 3)],
    )
    provider = SplitFallbackProvider()

    converted, report, usage = convert_chunk_with_fallback(
        chapter,
        provider,
        tmp_path,
        [],
        [],
        protected_terms=["韩立"],
        raw_name_prefix="chapter_0001_chunk_01",
    )

    assert report.passed
    assert [paragraph.id for paragraph in converted.paragraphs] == ["1-001", "1-002"]
    assert provider.paragraph_counts[:3] == [2, 2, 2]
    assert provider.paragraph_counts.count(1) == 2
    assert len(usage) == 2


def test_c1_uplift_replaces_anchored_chinese_with_c1_vocabulary():
    from ielts_novel.orchestrator import c1_share, uplift_c1_share
    from ielts_novel.processors.term_extractor import build_lookup

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他凝视着远方，心中十分紧张，嘴里念念有词。" * 6)])
    terms = [InsertedTerm(word="proceed", lemma="proceed", meaning="前行", part_of_speech="verb", cefr="B2")]
    text = "他凝视着远方 proceed（前行），心中十分紧张，嘴里念念有词。" + "他凝视着远方，心中十分紧张，嘴里念念有词。" * 5
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])
    catalog = [
        VocabularyItem(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="C1"),
        VocabularyItem(word="tension", lemma="tension", meaning="紧张", part_of_speech="noun", cefr="C1"),
        VocabularyItem(word="mutter", lemma="mutter", meaning="念念有词", part_of_speech="verb", cefr="C1"),
    ]

    before = c1_share(converted, lookup=build_lookup(catalog))
    uplifted = uplift_c1_share(source, converted, catalog, target_share=0.5, lookup=build_lookup(catalog))

    assert c1_share(uplifted, lookup=build_lookup(catalog)) > before
    assert any(f"（{meaning}）" in uplifted.paragraphs[0].converted_text for meaning in ("凝视", "紧张", "念念有词"))
    assert any(term.cefr == "C1" for term in uplifted.paragraphs[0].inserted_terms)


def test_c1_uplift_stops_when_the_target_share_is_already_met():
    from ielts_novel.orchestrator import c1_share, uplift_c1_share

    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他凝视着远方。" * 6)])
    terms = [InsertedTerm(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="C1")]
    text = "他 gaze（凝视）着远方。" + "他凝视着远方。" * 5
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])
    catalog = [VocabularyItem(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="C1")]

    assert c1_share(converted) == 1.0
    assert uplift_c1_share(source, converted, catalog, target_share=0.2) == converted

