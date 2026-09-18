from __future__ import annotations

import json

import pytest

from app.config import AppConfig
from app.knowledge import (
    collocation_rows,
    filter_terms,
    merge_terms,
    paginate,
    sort_terms,
)
from app.knowledge.novel import load_novel_terms

GLOSSARY = [
    {
        "word": "conserve",
        "lemma": "conserve",
        "meaning": "节约；保存",
        "part_of_speech": "verb",
        "cefr": "B2",
        "phonetic": "/kənˈsɜːv/",
        "collocation": "conserve water",
        "example_sentence": "They conserve water during droughts.",
        "first_chapter": 3,
        "last_chapter": 7,
        "occurrence_count": 4,
        "review_schedule": [4, 6, 10],
    },
    {
        "word": "adjust to",
        "lemma": "adjust to",
        "meaning": "适应",
        "part_of_speech": "verb",
        "cefr": "B1",
        "phonetic": "/əˈdʒʌst tuː/",
        "collocation": "adjust to change",
        "example_sentence": "It took him a while to adjust to the new environment.",
        "first_chapter": 7,
        "last_chapter": 7,
        "occurrence_count": 1,
    },
    {
        "word": "a waft of",
        "lemma": "a waft of",
        "meaning": "一股",
        "part_of_speech": "phrase",
        "cefr": "C1",
        "phonetic": "/ə wɑːft əv/",
        "collocation": "a waft of smoke",
        "example_sentence": "A waft of perfume drifted across the room.",
        "first_chapter": 2,
        "last_chapter": 2,
        "occurrence_count": 1,
    },
]


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        database_path=tmp_path / "output" / "state.db",
    )


@pytest.fixture
def novel_config(config):
    root = config.output_dir / "context-novel"
    (root / "chapter_json").mkdir(parents=True, exist_ok=True)
    (root / "glossary.json").write_text(
        json.dumps(GLOSSARY, ensure_ascii=False), encoding="utf-8"
    )
    (root / "index_entries.json").write_text(
        json.dumps([[2, "血尸"], [3, "古墓"], [7, "归途"]], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "chapter_json" / "第0003章.json").write_text(
        json.dumps(
            {
                "chapter_id": 3,
                "chapter_title": "古墓",
                "paragraphs": [
                    {
                        "id": "p1",
                        "converted_text": "they conserve water（节约用水）",
                        "plan": [{"zh": "节约用水", "en": "conserve water"}],
                        "inserted_terms": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config


def reading_term(**overrides) -> dict:
    row = {
        "key": "conserve",
        "headword": "conserve",
        "kind": "word",
        "phonetic": "",
        "meaning": "",
        "example": "",
        "collocations": [],
        "occurrences": 1,
        "category": "",
        "contexts": [],
        "sources": [
            {
                "kind": "reading",
                "label": "阅读练习 · 水的故事",
                "href": "/practice/u1/compare",
            }
        ],
        "status": "saved",
        "level": "B2",
        "level_source": "heuristic",
        "level_reason": "7 个字母",
        "pos_key": "verb",
        "pos_label": "动词",
        "pos_declared": "v.",
        "pos_inferred": False,
    }
    row.update(overrides)
    return row


def test_novel_terms_kind_level_and_contexts(novel_config):
    result = load_novel_terms(novel_config)

    assert result["available"] is True
    assert result["chapter_count"] == 3
    rows = {row["key"]: row for row in result["terms"]}
    assert rows["conserve"]["kind"] == "word"
    assert rows["adjust to"]["kind"] == "phrasal"
    assert rows["a waft of"]["kind"] == "collocation"
    assert rows["conserve"]["level"] == "B2"
    assert rows["conserve"]["level_source"] == "cefr"
    assert rows["conserve"]["occurrences"] == 4
    assert rows["conserve"]["sources"][0]["label"] == "情境小说 · 第 3 章 古墓"
    assert rows["conserve"]["sources"][0]["href"] == "/novel/preview/3"
    assert rows["conserve"]["contexts"] == [
        {"chapter_id": 3, "zh": "节约用水", "en": "conserve water"}
    ]


def test_missing_novel_directory_is_reported_not_fatal(config):
    assert load_novel_terms(config)["available"] is False


def test_merge_terms_combines_sources_and_counts(novel_config):
    novel = load_novel_terms(novel_config)["terms"]
    merged = merge_terms([reading_term()], novel)
    row = next(item for item in merged if item["key"] == "conserve")

    assert row["source_counts"] == {"reading": 1, "novel": 4}
    assert row["occurrences"] == 5
    assert row["meaning"] == "节约；保存"
    assert row["level_source"] == "cefr"
    assert {source["kind"] for source in row["sources"]} == {"reading", "novel"}
    assert len(row["sources"]) == 2


def test_collocation_rows_dedupe_and_point_back_to_the_word(novel_config):
    novel = load_novel_terms(novel_config)["terms"]
    merged = merge_terms([reading_term(collocations=["conserve water"])], novel)
    rows = collocation_rows(merged)
    phrases = [row["headword"] for row in rows]

    assert phrases.count("conserve water") == 1
    row = next(item for item in rows if item["headword"] == "conserve water")
    assert row["word"] == "conserve"
    assert row["meaning"] == "节约；保存"
    assert len(row["sources"]) == 2


def test_filter_terms_by_source_level_pos_text_and_flags(novel_config):
    novel = load_novel_terms(novel_config)["terms"]
    terms = merge_terms([reading_term()], novel)

    assert [row["headword"] for row in filter_terms(terms, source="reading")] == [
        "conserve"
    ]
    assert [row["headword"] for row in filter_terms(terms, source="both")] == [
        "conserve"
    ]
    assert len(filter_terms(terms, source="novel")) == 3
    assert [row["headword"] for row in filter_terms(terms, level="C1")] == ["a waft of"]
    assert sorted(row["headword"] for row in filter_terms(terms, pos="verb")) == [
        "adjust to",
        "conserve",
    ]
    assert [row["headword"] for row in filter_terms(terms, query="适应")] == [
        "adjust to"
    ]
    assert {row["headword"] for row in filter_terms(terms, repeats=True)} == {
        "conserve"
    }
    assert len(filter_terms(terms, has_collocation=True)) == 3


def test_sort_terms_orders_by_level_and_repeats(novel_config):
    novel = load_novel_terms(novel_config)["terms"]
    terms = merge_terms([reading_term()], novel)

    assert sort_terms(terms, "level")[0]["level"] == "C1"
    assert sort_terms(terms, "repeats")[0]["headword"] == "conserve"
    assert [row["headword"] for row in sort_terms(terms, "alpha")] == [
        "a waft of",
        "adjust to",
        "conserve",
    ]


def test_paginate_clamps_page_and_reports_range():
    rows = [{"headword": str(index)} for index in range(5)]
    first = paginate(rows, 1, 2)
    assert (first["total"], first["pages"], first["range_label"]) == (5, 3, "1–2")

    last = paginate(rows, 99, 2)
    assert last["page"] == 3
    assert last["has_next"] is False
    assert last["range_label"] == "5–5"

    assert paginate([], 1, 48)["range_label"] == "0"
