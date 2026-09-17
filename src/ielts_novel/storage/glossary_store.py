from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ielts_novel.models import VocabularyItem
from ielts_novel.storage.atomic import atomic_write_text

_STORE_LOCK = threading.Lock()


class GlossaryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, timeout=30)
        with _STORE_LOCK:
            self.connection.execute("PRAGMA busy_timeout=30000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS glossary (
                    lemma TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )"""
            )
            self.connection.commit()

    def record_occurrence(self, item: VocabularyItem, chapter_id: int) -> VocabularyItem:
        existing = self.get(item.lemma)
        if existing:
            item = existing.model_copy(update={
                "word": item.word,
                "meaning": item.meaning,
                "last_chapter": chapter_id,
                "occurrence_count": existing.occurrence_count + 1,
            })
        else:
            item = item.model_copy(update={
                "first_chapter": chapter_id,
                "last_chapter": chapter_id,
                "occurrence_count": 1,
                "review_schedule": [chapter_id + 1, chapter_id + 3, chapter_id + 7],
            })
        with self.connection:
            self.connection.execute(
                "INSERT INTO glossary(lemma,payload) VALUES(?,?) ON CONFLICT(lemma) DO UPDATE SET payload=excluded.payload",
                (item.lemma, item.model_dump_json()),
            )
        return item

    def get(self, lemma: str) -> VocabularyItem | None:
        row = self.connection.execute("SELECT payload FROM glossary WHERE lemma=?", (lemma,)).fetchone()
        return VocabularyItem.model_validate_json(row[0]) if row else None

    def all(self) -> list[VocabularyItem]:
        return [VocabularyItem.model_validate_json(row[0]) for row in self.connection.execute("SELECT payload FROM glossary ORDER BY lemma")]

    def export_json(self, path: str | Path) -> None:
        atomic_write_text(path, json.dumps([item.model_dump(mode="json") for item in self.all()], ensure_ascii=False, indent=2))

