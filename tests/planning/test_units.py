from __future__ import annotations

from app.models import Corpus, Difficulty, UnitStatus
from app.planning.units import (
    build_manifest,
    config_snapshot,
    default_question_types,
    plan_units,
    unit_source_text,
)

DEFAULT_TYPES = default_question_types(Difficulty.STANDARD)


def test_merges_short_chapters_without_crossing_four_chapters(config, chapters):
    units = plan_units(chapters[:5], config, Difficulty.STANDARD, DEFAULT_TYPES)

    assert len(units[0].source_chapter_ids) <= 4
    assert units[0].source_character_count <= 4000
    assert units[0].source_chapter_ids == ["c1", "c2", "c3", "c4"]
    assert units[0].limited_source is True


def test_merges_short_chapters_until_minimum_source_length(config, chapter):
    chapters = [chapter(f"c{index}", "甲" * 400, ordinal=index) for index in range(1, 5)]

    units = plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES)

    assert [unit.source_chapter_ids for unit in units] == [["c1", "c2"], ["c3", "c4"]]
    assert [unit.source_character_count for unit in units] == [800, 800]
    assert [unit.limited_source for unit in units] == [False, False]


def test_marks_irreducibly_short_unit(config, chapter):
    tiny = [chapter("c1", "甲" * 100)]

    unit = plan_units(tiny, config, Difficulty.STANDARD, DEFAULT_TYPES)[0]

    assert unit.limited_source is True
    assert unit.source_character_count == 100


def test_splits_six_thousand_character_chapter_on_paragraph_boundaries(config, chapter_with_paragraphs):
    source = chapter_with_paragraphs(["甲" * 1000] * 7)

    units = plan_units([source], config, Difficulty.STANDARD, DEFAULT_TYPES)

    assert len(units) == 2
    assert all(2500 <= unit.source_character_count <= 4500 for unit in units)
    assert [unit.source_chapter_ids for unit in units] == [["c1"], ["c1"]]


def test_rebalances_split_so_parts_stay_above_minimum(config, chapter_with_paragraphs):
    source = chapter_with_paragraphs(["甲" * 1000] * 6 + ["乙" * 100])

    units = plan_units([source], config, Difficulty.STANDARD, DEFAULT_TYPES)

    assert len(units) == 2
    assert all(2500 <= unit.source_character_count <= 4500 for unit in units)


def test_units_receive_stable_ids_ordinals_and_status(config, chapters):
    first = plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES, corpus_id="corpus-1")
    second = plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES, corpus_id="corpus-1")

    assert [unit.id for unit in first] == [unit.id for unit in second]
    assert [unit.ordinal for unit in first] == list(range(1, len(first) + 1))
    assert all(unit.status is UnitStatus.INDEXED for unit in first)
    assert all(unit.corpus_id == "corpus-1" for unit in first)
    assert all(unit.question_types == DEFAULT_TYPES for unit in first)
    assert all(len(unit.source_text_hash) == 64 for unit in first)


def test_units_reconstruct_exact_source_text(config, chapter_with_paragraphs):
    paragraphs = ["甲" * 1000] * 7
    source = chapter_with_paragraphs(paragraphs)

    units = plan_units([source], config, Difficulty.STANDARD, DEFAULT_TYPES)
    text = unit_source_text(units[0], {source.id: source})

    assert text == "\n".join(paragraphs[:4])
    # source_character_count counts source characters; the reconstruction adds
    # the paragraph separators used when the text is sent to the model.
    assert units[0].source_character_count == 4000
    assert len(text) == 4003
    assert units[0].source_spans[0].start == 0
    assert units[0].source_spans[0].end == 4000


def test_merged_unit_spans_cover_every_source_chapter(config, chapters):
    units = plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES)

    assert [span.chapter_id for span in units[0].source_spans] == ["c1", "c2", "c3", "c4"]
    assert [span.character_count for span in units[0].source_spans] == [100, 100, 100, 100]


def test_config_snapshot_excludes_paths_and_secrets(config):
    config = config.model_copy(update={"deepseek_api_key": "sk-secret-value"})

    snapshot = config_snapshot(config)

    assert "sk-secret-value" not in str(snapshot)
    assert "base_dir" not in snapshot
    assert snapshot["author_model"] == "deepseek-flash"
    assert snapshot["max_merged_chapters"] == 4


def test_manifest_summarizes_chapters_and_units(config, chapters):
    corpus = Corpus(
        id="corpus-1", name="Book", source_path="book.txt", source_hash="abc",
        format="txt", encoding="utf-8", chapter_count=len(chapters), parser_version="1",
    )
    units = plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES, corpus_id=corpus.id)

    manifest = build_manifest(
        corpus, chapters, units, confidence=0.93, diagnostics=["ordinal_gap"],
    )

    assert manifest.corpus.id == "corpus-1"
    assert manifest.chapter_count == 6
    assert manifest.unit_count == len(units) == 2
    assert manifest.total_characters == 600
    assert [summary.ordinal for summary in manifest.chapters] == [1, 2, 3, 4, 5, 6]
    assert manifest.chapters[0].title == "第1章"
    assert manifest.chapters[0].paragraph_count == 1
    assert manifest.confidence == 0.93
    assert manifest.diagnostics == ["ordinal_gap"]
    assert manifest.candidate_chapters == []


def test_empty_chapter_list_plans_no_units(config):
    assert plan_units([], config, Difficulty.STANDARD, DEFAULT_TYPES) == []
