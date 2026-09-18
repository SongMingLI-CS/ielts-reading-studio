from __future__ import annotations

from app.validators.passage import validate_passage


def test_accepts_grounded_passage_shape(valid_passage, brief):
    report = validate_passage("水资源有限，社区应谨慎使用。", brief, valid_passage)
    assert report.passed
    assert report.passage_word_count == 810
    assert report.paragraph_count == 6
    assert {"median_sentence_length", "type_token_ratio", "uncommon_word_ratio"} <= set(
        report.metrics
    )


def test_rejects_unsupported_numbers_and_entities(valid_passage, brief):
    valid_passage.paragraphs[0].text += " A 2025 Oxford study surveyed 8,000 people."
    report = validate_passage("没有年份、机构或数据。", brief, valid_passage)
    assert "unsupported_specific_fact" in report.codes
    issue = next(issue for issue in report.issues if issue.code == "unsupported_specific_fact")
    assert "A" in issue.affected_ids


def test_allows_specific_fact_that_is_present_in_source(valid_passage, brief):
    claim = "A 2025 Oxford study surveyed 8,000 people."
    valid_passage.paragraphs[0].text += " " + claim
    report = validate_passage("原文记载：" + claim, brief, valid_passage)
    assert "unsupported_specific_fact" not in report.codes


def test_allows_translated_names_but_still_checks_cross_language_numbers(valid_passage, brief):
    valid_passage.paragraphs[0].text += " Wu Xie met Zhang Qiling during the expedition."
    report = validate_passage("吴邪在探险中遇到了张起灵。", brief, valid_passage)
    assert "unsupported_specific_fact" not in report.codes

    valid_passage.paragraphs[0].text += " The event happened in 2025."
    report = validate_passage("吴邪在探险中遇到了张起灵。", brief, valid_passage)
    assert "unsupported_specific_fact" in report.codes


def test_rejects_bad_shape_and_noncontinuous_labels(valid_passage, brief):
    valid_passage.paragraphs = valid_passage.paragraphs[:3]
    valid_passage.paragraphs[-1].label = "D"
    report = validate_passage("原文", brief, valid_passage)
    assert {"paragraph_count_out_of_range", "paragraph_labels_invalid"} <= set(report.codes)


def test_rejects_missing_source_mapping_and_brief_coverage(valid_passage, brief):
    for paragraph in valid_passage.paragraphs:
        paragraph.source_ids = []
    valid_passage.source_coverage = {}
    report = validate_passage("原文", brief, valid_passage)
    assert {"paragraph_source_mapping_missing", "source_brief_not_covered"} <= set(report.codes)


def test_rejects_markdown_fence_and_placeholder(valid_passage, brief):
    valid_passage.paragraphs[0].text += " ``` TODO: add conclusion"
    report = validate_passage("原文", brief, valid_passage)
    assert {"markdown_fence_present", "unfinished_placeholder"} <= set(report.codes)


def test_empty_vocabulary_is_allowed(valid_passage, brief):
    report = validate_passage("原文", brief, valid_passage)

    assert report.metrics["vocabulary_entries"] == 0
    assert "vocabulary_too_short" not in report.codes


def test_half_filled_vocabulary_is_rejected(valid_passage, brief):
    """线上出现过：只给 word，其余字段全为 null —— 词汇板块会因此失效。"""
    from app.models import VocabularyEntry

    valid_passage.vocabulary = [
        VocabularyEntry(word="carefully"),
        VocabularyEntry(word="seasonal"),
    ]
    report = validate_passage("原文", brief, valid_passage)

    assert {"vocabulary_too_short", "vocabulary_meaning_missing"} <= set(report.codes)
    assert "vocabulary_pos_missing" in report.codes


def test_vocabulary_entries_must_be_real_terms_from_the_passage(valid_passage, brief):
    from app.models import VocabularyEntry

    valid_passage.vocabulary = [
        VocabularyEntry(
            word="not in the passage at all",
            part_of_speech="n.",
            chinese_meaning="不存在",
        ),
        VocabularyEntry(
            word="a whole sentence used as a term 2025",
            part_of_speech="n.",
            chinese_meaning="句子",
        ),
    ]
    report = validate_passage("原文", brief, valid_passage)

    assert "vocabulary_word_not_in_passage" in report.codes
    assert "vocabulary_entry_not_a_term" in report.codes


def test_complete_vocabulary_passes(valid_passage, brief):
    from app.models import VocabularyEntry

    words = [
        ("carefully", "adv.", "小心地"),
        ("seasonal", "adj.", "季节性的"),
        ("supplies", "n.", "供给"),
        ("decline", "v.", "下降"),
        ("communities", "n.", "社区"),
        ("manage", "v.", "管理"),
        ("water", "n.", "水"),
        ("seasonal supplies", "n.", "季节性供给"),
    ]
    valid_passage.vocabulary = [
        VocabularyEntry(word=word, part_of_speech=pos, chinese_meaning=meaning)
        for word, pos, meaning in words
    ]
    report = validate_passage("原文", brief, valid_passage)

    assert report.metrics["vocabulary_entries"] == 8
    assert not {
        "vocabulary_too_short",
        "vocabulary_meaning_missing",
        "vocabulary_pos_missing",
        "vocabulary_word_not_in_passage",
        "vocabulary_entry_not_a_term",
    } & set(report.codes)
