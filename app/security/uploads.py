"""Upload hygiene: extension allow-lists, content sniffing and display-name safety.

The browser-provided filename, MIME type and extension are all untrusted. Files are
written to server-generated paths, and the extension is only accepted when it is on the
allow-list *and* (for ZIP-based formats) the bytes actually look like that format.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

#: Leading bytes that must be present for a container format. Plain text has no signature.
MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".epub": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".txt": (),
}

_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f\"\\/]+")


def safe_display_name(filename: str | None, *, fallback: str = "upload") -> str:
    """Basename-only, control-character-free name for messages and logs."""

    if not filename:
        return fallback
    # Both separators are stripped: a Windows-style path is a path on the server too.
    candidate = PureWindowsPath(PurePosixPath(filename).name).name
    cleaned = _UNSAFE_NAME.sub("_", candidate).strip(" .")
    return cleaned[:120] or fallback


def normalize_extension(filename: str | None) -> str:
    """Case-folded suffix of the *basename*, so ``a/b/../x.TXT`` becomes ``.txt``."""

    return PurePosixPath(safe_display_name(filename, fallback="")).suffix.casefold()


def validate_extension(filename: str | None, allowed: set[str]) -> str:
    """Return the accepted extension or raise :class:`ValueError`."""

    extension = normalize_extension(filename)
    if extension not in allowed:
        raise ValueError(f"不支持的文件类型: {extension or '无扩展名'}")
    return extension


def check_magic(extension: str, head: bytes) -> bool:
    """True when the first bytes match the expected container for ``extension``."""

    prefixes = MAGIC_PREFIXES.get(extension, ())
    if not prefixes:
        return True
    return any(head.startswith(prefix) for prefix in prefixes)
