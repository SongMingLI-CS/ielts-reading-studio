from __future__ import annotations

import json

import pytest
from ielts_novel.models import VocabularyItem
from ielts_novel.processors.vocabulary_selector import (
    VocabularyCatalogError,
    VocabularySelector,
    validate_catalog,
)


def _items(count=100):
    levels = ["B1"] * 20 + ["B2"] * 60 + ["C1"] * 20
    categories = ["verb"] * 25 + ["adjective_adverb"] * 25 + ["noun"] * 20 + ["collocation"] * 20 + ["phrasal_verb"] * 10
    return [VocabularyItem(word=f"term{i}", lemma=f"term{i}", meaning=f"义{i}", part_of_speech=categories[i % 100], cefr=levels[i % 100]) for i in range(count)]


@pytest.mark.parametrize(("chapter", "new_ratio"), [(1, 0.6), (101, 0.4), (401, 0.2)])
def test_phase_ratios_and_deterministic_selection(chapter, new_ratio):
    selector = VocabularySelector(_items())
    first = selector.select(chapter, 50, seen_lemmas={f"term{i}" for i in range(50)})
    second = selector.select(chapter, 50, seen_lemmas={f"term{i}" for i in range(50)})
    assert first == second
    assert len(first.new) == round(50 * new_ratio)
    assert len(first.review) == 50 - len(first.new)


def test_rejects_too_basic_terms():
    with pytest.raises(VocabularyCatalogError, match="低价值"):
        VocabularySelector([VocabularyItem(word="good", lemma="good", meaning="好", part_of_speech="adjective", cefr="B1")])


def test_full_catalog_requires_5000_to_7000_unique_entries(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([item.model_dump() for item in _items()], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VocabularyCatalogError, match="5000"):
        validate_catalog(path, require_full=True)


def test_first_chapter_reallocates_unavailable_review_slots_to_new_words():
    plan = VocabularySelector(_items()).select(1, 50, seen_lemmas=set())
    assert len(plan.new) == 50
    assert not plan.review
