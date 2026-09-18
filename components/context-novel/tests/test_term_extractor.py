from __future__ import annotations

from ielts_novel.models import ConvertedChapter, ConvertedParagraph, InsertedTerm
from ielts_novel.processors.term_extractor import (
    build_lookup,
    extract_occurrences,
    normalize_chapter,
    repair_chapter_stacking,
)

CATALOG = [
    InsertedTerm(word="conceal", lemma="conceal", meaning="掩饰", part_of_speech="verb", cefr="B2", phonetic="/kənˈsiːl/"),
    InsertedTerm(word="under pressure", lemma="under pressure", meaning="承受压力", part_of_speech="phrase", cefr="B2"),
    InsertedTerm(word="nervousness", lemma="nervousness", meaning="紧张", part_of_speech="noun", cefr="B2"),
]


def _chapter(text: str, declared: list[InsertedTerm] | None = None) -> ConvertedChapter:
    return ConvertedChapter(chapter_id=1, chapter_title="第一章", paragraphs=[ConvertedParagraph(id="1-001", converted_text=text, inserted_terms=declared or [])])


def test_extracts_annotated_and_bare_occurrences_in_text_order():
    text = "她试图 conceal（掩饰）自己的 nervousness（紧张），同一句 under pressure（承受压力）。他再次 conceal。"
    occurrences = extract_occurrences(text, lookup=build_lookup(CATALOG))
    assert [item.term.word for item in occurrences] == ["conceal", "nervousness", "under pressure", "conceal"]
    assert [item.term.meaning for item in occurrences] == ["掩饰", "紧张", "承受压力", "掩饰"]
    assert [item.annotated for item in occurrences] == [True, True, True, False]


def test_extracts_multi_word_phrase_ending_with_function_word():
    occurrences = extract_occurrences("他必须 adapt to（适应）新环境。", lookup={})
    assert [item.term.word for item in occurrences] == ["adapt to"]
    assert occurrences[0].term.meaning == "适应"


def test_extracts_phrase_containing_single_letter_token():
    declared = [InsertedTerm(word="earn a lot of money", lemma="earn a lot of money", meaning="挣大钱", part_of_speech="phrase", cefr="B2")]
    converted = _chapter("可以进城能 earn a lot of money（挣大钱）还是明白的。", declared)
    occurrences = extract_occurrences(converted.paragraphs[0].converted_text, lookup=build_lookup(declared))
    assert [item.term.word for item in occurrences] == ["earn a lot of money"]
    assert occurrences[0].term.meaning == "挣大钱"
    assert normalize_chapter(converted).paragraphs[0].inserted_terms[0].meaning == "挣大钱"


def test_single_letters_are_never_extracted_as_terms():
    occurrences = extract_occurrences("他 a b c 走了。", lookup={})
    assert occurrences == []


def test_keeps_known_suffix_when_latin_run_precedes_annotation():
    occurrences = extract_occurrences("他 conceal（掩饰） his nervousness（紧张）。", lookup=build_lookup(CATALOG))
    assert [item.term.word for item in occurrences] == ["conceal", "nervousness"]


def test_unresolved_bare_word_has_empty_meaning():
    occurrences = extract_occurrences("这里 zzzunknown 出现了。", lookup=build_lookup(CATALOG))
    assert [item.term.word for item in occurrences] == ["zzzunknown"]
    assert occurrences[0].term.meaning == ""


def test_normalize_chapter_drops_terms_that_are_absent_from_text():
    ghost = InsertedTerm(word="ghostword", lemma="ghostword", meaning="不存在", part_of_speech="noun", cefr="B2")
    assert normalize_chapter(_chapter("这里没有英文。", [ghost])).paragraphs[0].inserted_terms == []


def test_normalize_chapter_counts_every_text_occurrence():
    declared = [InsertedTerm(word="conceal", lemma="conceal", meaning="掩饰", part_of_speech="verb", cefr="B2")]
    normalized = normalize_chapter(_chapter("他 conceal（掩饰）了心情，又一次 conceal。", declared))
    assert [item.word for item in normalized.paragraphs[0].inserted_terms] == ["conceal", "conceal"]
    assert normalized.paragraphs[0].inserted_terms[1].meaning == "掩饰"


def test_normalize_chapter_prefers_catalog_metadata_for_bare_repeats():
    declared = [InsertedTerm(word="conceal", lemma="conceal", meaning="掩饰", part_of_speech="verb", cefr="B1")]
    normalized = normalize_chapter(_chapter("他 conceal（掩饰）了心情，又一次 conceal。", declared), lookup=build_lookup(CATALOG))
    assert normalized.paragraphs[0].inserted_terms[1].phonetic == "/kənˈsiːl/"


def test_repair_chapter_stacking_reverts_fourth_item_and_adds_first_annotation():
    declared = [InsertedTerm(word=f"word{letter}", lemma=f"word{letter}", meaning=f"义{letter}", part_of_speech="noun", cefr="B2") for letter in "abcd"]
    repaired = repair_chapter_stacking(_chapter("worda wordb wordc wordd。", declared))
    assert repaired.paragraphs[0].converted_text == "worda（义a） wordb（义b） wordc（义c） 义d。"
    assert [item.word for item in repaired.paragraphs[0].inserted_terms] == ["worda", "wordb", "wordc"]


def test_repair_chapter_stacking_drops_stray_unresolved_fragments():
    declared = [InsertedTerm(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="B2")]
    chapter = _chapter("她轻轻地抚mo着它，gaze（凝视）着它看。", declared)
    repaired = repair_chapter_stacking(chapter)
    assert "mo" not in repaired.paragraphs[0].converted_text
    assert repaired.paragraphs[0].converted_text == "她轻轻地抚着它，gaze（凝视）着它看。"
    assert [item.lemma for item in repaired.paragraphs[0].inserted_terms] == ["gaze"]
    declared = [InsertedTerm(word=f"word{letter}", lemma=f"word{letter}", meaning=f"义{letter}", part_of_speech="noun", cefr="B2") for letter in "abcd"]
    repaired = repair_chapter_stacking(_chapter("worda（义a） wordb（义b） wordc（义c）。wordd（义d）。", declared))
    assert repaired.paragraphs[0].converted_text == "worda（义a） wordb（义b） wordc（义c）。wordd（义d）。"
    assert len(repaired.paragraphs[0].inserted_terms) == 4
