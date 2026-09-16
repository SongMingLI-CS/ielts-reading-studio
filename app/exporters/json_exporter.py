from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app.models import ReadingPackage


def export_json(package: ReadingPackage, path: str | Path) -> Path:
    _require_validated(package)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f"{destination.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                package.model_dump(mode="json"),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _require_validated(package: ReadingPackage) -> None:
    if not package.quality_report.passed or package.unit.status.value != "completed":
        raise ValueError("Only completed, validated packages can be exported")

