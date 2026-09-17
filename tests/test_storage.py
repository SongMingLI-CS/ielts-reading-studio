from __future__ import annotations

import json

from ielts_novel.models import VocabularyItem
from ielts_novel.storage.glossary_store import GlossaryStore
from ielts_novel.storage.progress_store import ProgressStore


def test_progress_recovers_running_and_skips_completed(tmp_path):
    store = ProgressStore(tmp_path / "state.db", tmp_path / "progress.json")
    assert store.claim(1)
    store.close()

    resumed = ProgressStore(tmp_path / "state.db", tmp_path / "progress.json")
    assert resumed.claim(1)
    resumed.complete(1, inserted_count=42)
    assert not resumed.claim(1)
    assert resumed.completed_ids() == [1]
    assert json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))["completed"] == [1]


def test_failed_chapters_can_be_selected(tmp_path):
    store = ProgressStore(tmp_path / "state.db", tmp_path / "progress.json")
    store.claim(3)
    store.fail(3, "quality_error")
    assert store.failed_ids() == [3]


def test_glossary_updates_occurrence_and_review_schedule(tmp_path):
    store = GlossaryStore(tmp_path / "state.db")
    item = VocabularyItem(word="conceal", lemma="conceal", meaning="掩饰", part_of_speech="verb", cefr="B2")
    store.record_occurrence(item, chapter_id=1)
    store.record_occurrence(item, chapter_id=4)
    stored = store.get("conceal")
    assert stored.occurrence_count == 2
    assert stored.first_chapter == 1
    assert stored.last_chapter == 4
    assert stored.review_schedule == [2, 4, 8]



def test_atomic_write_retries_transient_permission_errors(tmp_path, monkeypatch):
    import pathlib

    from ielts_novel.storage import atomic

    target = tmp_path / "progress.json"
    calls = {"count": 0}
    real_replace = pathlib.Path.replace

    def flaky_replace(self, other):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise PermissionError("file is being used by another process")
        return real_replace(self, other)

    monkeypatch.setattr(pathlib.Path, "replace", flaky_replace)
    monkeypatch.setattr(atomic.time, "sleep", lambda _seconds: None)

    atomic.atomic_write_text(target, "{\"ok\": true}")

    assert target.read_text(encoding="utf-8") == "{\"ok\": true}"
    assert calls["count"] == 3

