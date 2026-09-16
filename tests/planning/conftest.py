from __future__ import annotations

import pytest

from app.config import AppConfig
from app.models import SourceChapter, SourceParagraph


@pytest.fixture
def config(tmp_path):
    return AppConfig(base_dir=tmp_path)


@pytest.fixture
def chapter_with_paragraphs():
    def build(paragraphs, *, chapter_id="c1", ordinal=1):
        character_count = sum(len(paragraph) for paragraph in paragraphs)
        return SourceChapter(
            id=chapter_id,
            ordinal=ordinal,
            chapter_title=f"第{ordinal}章",
            paragraphs=[
                SourceParagraph(id=f"{chapter_id}-p{index}", text=text)
                for index, text in enumerate(paragraphs, start=1)
            ],
            character_count=character_count,
            source_offsets={"start": 0, "end": character_count},
        )

    return build


@pytest.fixture
def chapter(chapter_with_paragraphs):
    def build(chapter_id, text, ordinal=1):
        return chapter_with_paragraphs([text], chapter_id=chapter_id, ordinal=ordinal)

    return build


@pytest.fixture
def chapters(chapter):
    return [chapter(f"c{index}", "甲" * 100, ordinal=index) for index in range(1, 7)]
