"""Consistent SQLite snapshots shared by the backup page, the CLI and the deploy script.

Copying ``state.db`` with ``cp`` while the service runs can capture a torn WAL state.
SQLite's online backup API always produces a complete, consistent database, which is
why every snapshot path in this project goes through this function.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def snapshot_database(database: str | Path, target: str | Path) -> bool:
    """Write a consistent copy of ``database`` to ``target``.

    Returns ``False`` when the source does not exist yet (a fresh install has nothing to
    snapshot), and creates the target's parent directory when needed.
    """

    source_path = Path(database)
    target_path = Path(target)
    if not source_path.exists():
        return False
    if source_path.resolve() == target_path.resolve():
        raise ValueError("快照目标不能与数据库文件相同")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return True
