from __future__ import annotations

from app.knowledge import align


def test_align_pairs_inflected_forms():
    result = align(
        "Governments conserve water",
        "Local councils conserving rivers acted early",
    )
    assert {"prompt": "conserve", "quote": "conserving"} in result["form_pairs"]
    assert result["coverage"] > 0


def test_align_marks_both_sides_when_wording_differs():
    result = align(
        "The main reason for the decline",
        "Clean water requires careful local planning.",
    )
    assert result["prompt_markers"] == ["main", "reason", "decline"]
    assert "planning" in result["quote_markers"]
    assert "water" not in result["prompt_markers"]
    assert result["hint"].startswith("题干与原文换了说法")


def test_align_reports_verbatim_question():
    result = align(
        "Clean water requires careful local planning",
        "Clean water requires careful local planning.",
    )
    assert result["prompt_markers"] == []
    assert result["quote_markers"] == []
    assert result["hint"].startswith("题干与原文用词一致")


def test_align_handles_empty_quote():
    result = align("Which paragraph mentions scarcity?", "")
    assert result["quote_markers"] == []
    assert result["coverage"] == 0.0
    assert "scarcity" in result["prompt_markers"]
