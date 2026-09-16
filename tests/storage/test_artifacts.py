from __future__ import annotations

import json

from app.storage.artifacts import ArtifactStore


def test_write_json_is_atomic_and_utf8(tmp_path):
    store = ArtifactStore(tmp_path)

    path = store.write_json("packages/u1.json", {"title": "水资源"})

    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "水资源"
    assert list(path.parent.glob("*.tmp")) == []


def test_raw_response_path_is_scoped_to_unit_stage_and_attempt(tmp_path):
    store = ArtifactStore(tmp_path)

    path = store.write_raw_response("unit-1", "author", 2, {"response": "ok"})

    assert path == tmp_path / "raw_responses" / "unit-1" / "author-attempt-2.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"response": "ok"}
