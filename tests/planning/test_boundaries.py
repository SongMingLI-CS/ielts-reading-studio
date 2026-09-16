import pytest

from app.models import SourceChapter, SourceParagraph
from app.planning.boundaries import BoundaryEdit, apply_boundary_edits


def chapter(chapter_id: str, ordinal: int, paragraphs: list[str]):
    return SourceChapter(
        id=chapter_id,
        ordinal=ordinal,
        chapter_title=f"Chapter {ordinal}",
        paragraphs=[
            SourceParagraph(id=f"{chapter_id}-p{index}", text=text)
            for index, text in enumerate(paragraphs, start=1)
        ],
        character_count=sum(map(len, paragraphs)),
    )


def test_split_and_rename_create_auditable_new_boundaries():
    chapters = [chapter("c1", 1, ["first", "second", "third"])]
    split = apply_boundary_edits(
        chapters,
        [
            BoundaryEdit(
                action="split_before_paragraph",
                chapter_id="c1",
                paragraph_index=1,
                title="New section",
            )
        ],
    )

    assert [value.ordinal for value in split] == [1, 2]
    assert [len(value.paragraphs) for value in split] == [1, 2]
    assert split[1].chapter_title == "New section"
    assert split[0].id != chapters[0].id


def test_merge_requires_a_following_chapter():
    with pytest.raises(ValueError, match="following"):
        apply_boundary_edits(
            [chapter("c1", 1, ["only"])],
            [BoundaryEdit(action="merge_next", chapter_id="c1")],
        )
