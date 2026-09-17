from __future__ import annotations

import string

from ielts_novel.models import Chapter, ConvertedChapter, ConvertedParagraph, InsertedTerm, Paragraph
from ielts_novel.processors.quality_checker import QualityChecker, repair_english_stacking


def _source(chars=500):
    return Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="韩立看见山路。" + "他缓慢前行。" * ((chars - 7) // 6))])


def _converted(count, *, pid="1-001", text=None):
    words = ["term" + string.ascii_lowercase[i // 26] + string.ascii_lowercase[i % 26] for i in range(count)]
    terms = [InsertedTerm(word=word, lemma=word, meaning="含义", part_of_speech="noun", cefr="B2") for word in words]
    sentences = ["韩立看见山路。"]
    sentences.extend(f"他缓慢前行{' ' + words[i] + '（含义）' if i < count else ''}。" for i in range(82))
    rendered = "".join(sentences)
    return ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id=pid, converted_text=text or rendered, inserted_terms=terms)])


def test_accepts_density_inside_range():
    report = QualityChecker().check(_source(500), _converted(28), protected_terms=["韩立"])
    assert report.passed
    assert 20 <= report.metrics["density_per_500"] <= 35


def test_rejects_low_and_high_density():
    assert "density_too_low" in QualityChecker().check(_source(500), _converted(10)).issues
    assert "density_too_high" in QualityChecker().check(_source(500), _converted(40)).issues


def test_chunk_check_can_tolerate_unavoidable_integer_rounding():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他缓慢地前行。" * 5)])
    term = InsertedTerm(word="proceed", lemma="proceed", meaning="前行", part_of_speech="verb", cefr="B2")
    converted = ConvertedChapter(
        chapter_id=1,
        chapter_title="第一章",
        paragraphs=[ConvertedParagraph(id="1-001", converted_text="他缓慢 proceed（前行）。" + "他缓慢地前行。" * 4, inserted_terms=[term])],
    )

    assert "density_too_low" in QualityChecker().check(source, converted).issues
    rounded = QualityChecker(allow_discrete_rounding=True).check(source, converted)
    assert "density_too_low" not in rounded.issues


def test_block_check_tolerates_short_paragraph_rounding_but_chapter_check_does_not():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他缓慢地前行。" * 7)])  # 42 chars
    terms = [InsertedTerm(word=f"term{letter}", lemma=f"term{letter}", meaning="前行", part_of_speech="verb", cefr="B2") for letter in "abcde"]
    text = "他 terma（前行） termb（前行） termc（前行） termd（前行） terme（前行）缓慢地前行。" + "他缓慢地前行。" * 6
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])

    assert 5 * 500 / 42 > 35
    assert "density_too_high" not in QualityChecker(allow_discrete_rounding=True).check(source, converted).issues
    assert "density_too_high" in QualityChecker().check(source, converted).issues


def test_declared_but_unwritten_terms_never_count_towards_density():
    converted = _converted(28, text="韩立看见山路。 conceal（掩饰）。")
    report = QualityChecker().check(_source(500), converted)
    assert "term_not_in_text" in report.issues
    assert "density_too_low" in report.issues
    assert report.metrics["inserted_count"] == 1


def test_density_counts_every_occurrence_including_bare_repeats():
    declared = [InsertedTerm(word="proceed", lemma="proceed", meaning="前行", part_of_speech="verb", cefr="B2")]
    text = "他缓慢 proceed（前行）。" + "他缓慢 proceed。" * 27
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=declared)])
    report = QualityChecker().check(_source(500), converted)
    assert report.metrics["inserted_count"] == 28
    assert report.metrics["distinct_terms"] == 1
    assert report.passed


def test_rejects_missing_or_duplicate_paragraph_ids():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="甲"), Paragraph(id="1-002", text="乙")])
    missing = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="甲")])
    duplicate = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="甲"), ConvertedParagraph(id="1-001", converted_text="乙")])
    assert "paragraph_ids_mismatch" in QualityChecker().check(source, missing).issues
    assert "duplicate_paragraph_ids" in QualityChecker().check(source, duplicate).issues


def test_rejects_changed_name_markdown_and_unannotated_first_terms():
    converted = _converted(28, text="```json\n李立看见山路。 conceal")
    report = QualityChecker().check(_source(500), converted, protected_terms=["韩立"])
    assert {"protected_term_changed", "markdown_or_explanation", "missing_inline_meaning"} <= set(report.issues)


def test_rejects_same_length_but_unrelated_rewrite():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="韩立沿着崎岖山路走进安静村庄。" * 30)])
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="海上风暴突然摧毁所有船只和港口。" * 30, inserted_terms=[])])
    assert "content_similarity_too_low" in QualityChecker(minimum_density=0).check(source, converted).issues


def test_rejects_english_inserted_by_appending_new_sentences():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="此时的韩立正处于迷迷糊糊之间，脑中一直残留着念头。" * 5)])
    added = "此时的韩立正处于迷迷糊糊之间的 drowsy（困倦）状态，脑中一直残留着 notion（念头）。（这里补充了一句原文没有的解释性内容，用来展开说明他的心情。）" * 5
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=added, inserted_terms=[])])
    assert "content_added" in QualityChecker(minimum_density=0).check(source, converted).issues


def test_accepts_in_place_replacement_of_chinese_words():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="她试图掩饰自己的紧张。" * 40)])
    replaced = "她试图 conceal（掩饰）自己的 nervousness（紧张）。" * 40
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=replaced, inserted_terms=[])])
    report = QualityChecker(minimum_density=0).check(source, converted)
    assert "content_added" not in report.issues
    assert "content_similarity_too_low" not in report.issues


def test_rejects_more_than_three_learning_items_in_one_sentence():
    source = Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id="1-001", text="他谨慎地观察四周。" * 100)])
    terms = [InsertedTerm(word=f"word{letter}", lemma=f"word{letter}", meaning="义", part_of_speech="noun", cefr="B2") for letter in "abcd"]
    text = "他 worda（义） wordb（义） wordc（义） wordd（义）。" + "他谨慎地观察四周。" * 100
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=terms)])
    assert "english_stacking" in QualityChecker(minimum_density=0).check(source, converted).issues


def test_repairs_fourth_learning_item_back_to_chinese_meaning():
    terms = [InsertedTerm(word=f"word{letter}", lemma=f"word{letter}", meaning=f"义{letter}", part_of_speech="noun", cefr="B2") for letter in "abcd"]
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="worda（义a） wordb（义b） wordc（义c） wordd（义d）。", inserted_terms=terms)])
    repaired = repair_english_stacking(converted)
    assert repaired.paragraphs[0].converted_text == "worda（义a） wordb（义b） wordc（义c） 义d。"
    assert len(repaired.paragraphs[0].inserted_terms) == 3


def test_repairs_plain_english_stacking_and_adds_first_meaning():
    terms = [InsertedTerm(word=f"word{letter}", lemma=f"word{letter}", meaning=f"义{letter}", part_of_speech="noun", cefr="B2") for letter in "abcd"]
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="worda wordb wordc wordd。", inserted_terms=terms)])
    repaired = repair_english_stacking(converted)
    assert repaired.paragraphs[0].converted_text == "worda（义a） wordb（义b） wordc（义c） 义d。"
    assert len(repaired.paragraphs[0].inserted_terms) == 3


def test_removes_declared_term_that_is_absent_from_text():
    ghost = InsertedTerm(word="ghostword", lemma="ghostword", meaning="不存在", part_of_speech="noun", cefr="B2")
    converted = ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text="这里没有任何英文。", inserted_terms=[ghost])])
    repaired = repair_english_stacking(converted)
    assert repaired.paragraphs[0].inserted_terms == []
