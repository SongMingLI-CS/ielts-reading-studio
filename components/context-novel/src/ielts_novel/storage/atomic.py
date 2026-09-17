from __future__ import annotations

import time
from pathlib import Path

RETRIES = 6
BACKOFF_SECONDS = 0.2


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8", suffix: str = ".tmp") -> Path:
    """Write a file atomically, retrying transient Windows sharing violations.

    Windows (or a synchronising folder such as OneDrive) can briefly hold a handle on a file, which
    turns ``os.replace`` into ``PermissionError``. An unattended 2,000 chapter run must survive that
    instead of failing the chapter, so the replace step is retried with a short backoff.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + suffix)
    temporary.write_text(text, encoding=encoding)
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            temporary.replace(target)
            return target
        except PermissionError as exc:  # file briefly locked by another process or the sync client
            last_error = exc
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
    if last_error is not None:
        raise last_error
    return target


def atomic_save_workbook(workbook, path: str | Path) -> Path:
    """Save an openpyxl workbook atomically (same retry policy as :func:`atomic_write_text`)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp" + target.suffix)
    workbook.save(str(temporary))
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            temporary.replace(target)
            return target
        except PermissionError as exc:
            last_error = exc
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
    if last_error is not None:
        raise last_error
    return target


def atomic_save_document(document, path: str | Path) -> Path:
    """Save a python-docx document atomically (same retry policy as :func:`atomic_write_text`)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp" + target.suffix)
    document.save(str(temporary))
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            temporary.replace(target)
            return target
        except PermissionError as exc:
            last_error = exc
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
    if last_error is not None:
        raise last_error
    return target
