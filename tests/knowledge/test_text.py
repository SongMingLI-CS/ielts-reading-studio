from __future__ import annotations

from app.knowledge import content_words, highlight, is_phrase, unique_content_words
from app.knowledge.text import form_pattern


def test_content_words_drops_function_words_and_keeps_numbers():
    assert content_words("The main reason for the 3 declines in 2024") == [
        "main",
        "reason",
        "3",
        "declines",
        "2024",
    ]


def test_content_words_handles_empty_text():
    assert content_words("") == []
    assert unique_content_words("") == []


def test_unique_content_words_keeps_first_occurrence_order():
    assert unique_content_words("water supplies and water quality") == [
        "water",
        "supplies",
        "quality",
    ]


def test_is_phrase_detects_multi_word_entries():
    assert is_phrase("water conservation") is True
    assert is_phrase("chain-reaction") is True
    assert is_phrase("conserve") is False
    assert is_phrase("   ") is False


def test_form_pattern_matches_inflections_but_not_other_words():
    pattern = form_pattern("conserve")
    assert pattern.search("Water conservation matters") is None
    assert pattern.search("They conserve water") is not None
    assert pattern.search("Conserving water") is not None
    assert pattern.search("conservation taxes") is None


def test_form_pattern_matches_phrase_with_flexible_spacing():
    pattern = form_pattern("water conservation")
    assert pattern.search("water  conservation is key") is not None
    assert pattern.search("waterconservation") is None


def test_highlight_wraps_matches_and_escapes_html():
    html = highlight("Water conservation <script> matters.", ["water conservation"])
    assert '<mark class="kw">Water conservation</mark>' in html
    assert "&lt;script&gt;" in html


def test_highlight_returns_escaped_text_when_no_match():
    html = highlight("Nothing to mark here", ["conservation"])
    assert html == "Nothing to mark here"


def test_highlight_supports_custom_class():
    html = highlight("They conserve water.", ["conserve"], "kw-coll")
    assert '<mark class="kw-coll">conserve</mark>' in html
