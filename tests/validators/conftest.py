from __future__ import annotations

import pytest

from app.models import (
    BriefItem,
    Difficulty,
    PassageParagraph,
    ReadingPassage,
    SourceBrief,
)


@pytest.fixture
def brief():
    return SourceBrief(
        core_facts=[BriefItem(id="f1", text="Water supplies are limited.")],
        core_claims=[BriefItem(id="c1", text="Communities should conserve water.")],
    )


@pytest.fixture
def valid_passage():
    sentence = (
        "Communities manage water carefully because seasonal supplies can decline. "
    )
    paragraphs = [
        PassageParagraph(label=label, text=sentence * 15, source_ids=["f1", "c1"])
        for label in "ABCDEF"
    ]
    text = " ".join(paragraph.text for paragraph in paragraphs)
    return ReadingPassage(
        title="Managing Water",
        difficulty=Difficulty.STANDARD,
        word_count=len(text.split()),
        paragraphs=paragraphs,
        source_coverage={"f1": ["A"], "c1": ["B"]},
    )
