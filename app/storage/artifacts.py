from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel


class ArtifactStore:
    """Writes generation artifacts atomically underneath one output directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_json(self, relative_path: str | Path, value: object) -> Path:
        path = self._destination(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f"{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._json_value(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return path

    def write_raw_response(self, unit_id: str, stage: str, attempt: int, value: object) -> Path:
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        return self.write_json(
            Path("raw_responses") / unit_id / f"{stage}-attempt-{attempt}.json",
            value,
        )

    def write_package(self, unit_id: str, value: object) -> Path:
        return self.write_json(Path("packages") / f"{unit_id}.json", value)

    def write_stage_payload(self, unit_id: str, stage: str, attempt: int, value: object) -> Path:
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        return self.write_json(
            Path("stage_payloads") / unit_id / f"{stage}-attempt-{attempt}.json",
            value,
        )

    @staticmethod
    def _json_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    def _destination(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("artifact paths must be relative")
        root = self.root.resolve()
        destination = (root / relative).resolve()
        if destination != root and root not in destination.parents:
            raise ValueError("artifact path must stay within the artifact root")
        return destination
