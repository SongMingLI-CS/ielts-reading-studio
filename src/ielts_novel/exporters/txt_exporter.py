from __future__ import annotations

from pathlib import Path

from ielts_novel.models import ConvertedChapter
from ielts_novel.storage.atomic import atomic_write_text

CHAPTER_SEPARATOR = "\n\n"


def render_chapters_txt(chapters: list[ConvertedChapter], *, include_glossary: bool = False, separator: str = CHAPTER_SEPARATOR) -> str:
    """Render chapters as one plain-text novel file for e-reader apps.

    Chapter titles are written on their own line followed by a blank line, which is the layout most
    Chinese reading apps (阅读/掌阅/静读天下) use to build their table of contents.
    """
    blocks: list[str] = []
    for chapter in chapters:
        lines = [chapter.chapter_title.strip(), ""]
        for paragraph in chapter.paragraphs:
            text = paragraph.converted_text.strip()
            if text:
                lines.append(text)
            lines.append("")
        if include_glossary:
            terms = []
            seen: set[str] = set()
            for paragraph in chapter.paragraphs:
                for term in paragraph.inserted_terms:
                    if term.lemma.lower() in seen:
                        continue
                    seen.add(term.lemma.lower())
                    terms.append(term)
            if terms:
                lines.append("本章核心词汇")
                lines.append("")
                for term in terms:
                    detail = f"{term.word}  {term.phonetic}  {term.part_of_speech}  {term.meaning}  [{term.cefr}]"
                    if term.collocation:
                        detail += f"  搭配：{term.collocation}"
                    lines.append(detail)
                lines.append("")
        blocks.append("\n".join(lines).rstrip())
    return separator.join(blocks) + "\n"


def export_chapters_txt(chapters: list[ConvertedChapter], path: str | Path, *, include_glossary: bool = False, encoding: str = "utf-8") -> Path:
    target = Path(path)
    content = render_chapters_txt(chapters, include_glossary=include_glossary)
    atomic_write_text(target, content, encoding=encoding)
    return target


def load_chapters(directory: str | Path, *, start: int, end: int) -> list[ConvertedChapter]:
    """Load consecutive converted chapters from the chapter_json store."""
    chapters: list[ConvertedChapter] = []
    for chapter_id in range(start, end + 1):
        path = Path(directory) / f"第{chapter_id:04d}章.json"
        if not path.exists():
            raise FileNotFoundError(f"缺少章节文件：{path}")
        chapters.append(ConvertedChapter.model_validate_json(path.read_text(encoding="utf-8")))
    return chapters
