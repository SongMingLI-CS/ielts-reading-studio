from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ielts_novel.storage.atomic import atomic_write_text

_STORE_LOCK = threading.Lock()
_RECOVERED: set[str] = set()


class ProgressStore:
    def __init__(self, db_path: str | Path, snapshot_path: str | Path):
        self.db_path = Path(db_path)
        self.snapshot_path = Path(snapshot_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, timeout=30)
        key = str(self.db_path.resolve())
        with _STORE_LOCK:
            self.connection.execute("PRAGMA busy_timeout=30000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS chapter_progress (
                    chapter_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    inserted_count INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            # Crash recovery must run once per process: resetting inside every new store would
            # clear the claim of chapters that another worker is currently processing.
            if key not in _RECOVERED:
                self.connection.execute("UPDATE chapter_progress SET status='pending' WHERE status='running'")
                _RECOVERED.add(key)
            self.connection.commit()

    def claim(self, chapter_id: int, *, allow_failed: bool = True) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            row = self.connection.execute("SELECT status FROM chapter_progress WHERE chapter_id=?", (chapter_id,)).fetchone()
            if row and row[0] == "completed":
                return False
            if row and row[0] == "failed" and not allow_failed:
                return False
            self.connection.execute(
                "INSERT INTO chapter_progress(chapter_id,status,updated_at) VALUES(?, 'running', ?) "
                "ON CONFLICT(chapter_id) DO UPDATE SET status='running', error_type=NULL, updated_at=excluded.updated_at",
                (chapter_id, now),
            )
        self._snapshot()
        return True

    def complete(self, chapter_id: int, inserted_count: int = 0) -> None:
        self._set(chapter_id, "completed", inserted_count, None)

    def fail(self, chapter_id: int, error_type: str) -> None:
        self._set(chapter_id, "failed", 0, error_type)

    def _set(self, chapter_id: int, status: str, inserted_count: int, error_type: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT INTO chapter_progress(chapter_id,status,inserted_count,error_type,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(chapter_id) DO UPDATE SET status=excluded.status, inserted_count=excluded.inserted_count, error_type=excluded.error_type, updated_at=excluded.updated_at",
                (chapter_id, status, inserted_count, error_type, now),
            )
        self._snapshot()

    def _ids(self, status: str) -> list[int]:
        return [row[0] for row in self.connection.execute("SELECT chapter_id FROM chapter_progress WHERE status=? ORDER BY chapter_id", (status,))]

    def completed_ids(self) -> list[int]:
        return self._ids("completed")

    def failed_ids(self) -> list[int]:
        return self._ids("failed")

    def _snapshot(self) -> None:
        # Several chapter workers share one snapshot path. Serialising both the
        # database reads and atomic replace prevents workers from racing on the
        # same temporary file or replacing a newer snapshot with stale state.
        with _STORE_LOCK:
            payload = {
                "completed": self.completed_ids(),
                "failed": self.failed_ids(),
                "running": self._ids("running"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_text(
                self.snapshot_path,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

    def close(self) -> None:
        self.connection.close()


__all__ = ["ProgressStore"]
