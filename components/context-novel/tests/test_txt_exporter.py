from __future__ import annotations

from pathlib import Path

from ielts_novel.models import ConvertedChapter, ConvertedParagraph, InsertedTerm
from ielts_novel.exporters.txt_exporter import export_chapters_txt, render_chapters_txt


def _chapter(chapter_id: int, title: str, texts: list[str]) -> ConvertedChapter:
    return ConvertedChapter(
        chapter_id=chapter_id,
        chapter_title=title,
        paragraphs=[ConvertedParagraph(id=f"{chapter_id}-{index:03d}", converted_text=text) for index, text in enumerate(texts, 1)],
    )


def test_render_txt_puts_each_title_on_its_own_line():
    chapters = [_chapter(1, "第0001章 山边小村", ["二愣子 quiet（安静）地看着屋顶。", "他没有 respond（回应）。"]), _chapter(2, "第0002章 青牛镇", ["清晨的集市 very（非常）热闹。"])]

    content = render_chapters_txt(chapters)

    lines = content.splitlines()
    assert lines[0] == "第0001章 山边小村"
    assert lines[1] == ""
    assert lines[2] == "二愣子 quiet（安静）地看着屋顶。"
    assert "第0002章 青牛镇" in lines
    assert content.count("第000") == 2
    assert content.endswith("\n")


def test_txt_export_can_append_the_chapter_glossary():
    chapter = _chapter(1, "第0001章", ["他 gaze（凝视）着远方。"])
    term = InsertedTerm(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="B2", phonetic="/ɡeɪz/", collocation="gaze at")
    chapter.paragraphs[0].inserted_terms = [term]

    content = render_chapters_txt([chapter], include_glossary=True)

    assert "本章核心词汇" in content
    assert "gaze  /ɡeɪz/  verb  凝视  [B2]  搭配：gaze at" in content


def test_export_txt_writes_utf8_file(tmp_path):
    path = export_chapters_txt([_chapter(1, "第0001章", ["他缓慢前行。"])], tmp_path / "novel.txt")

    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("第0001章")
