"""Prove the hidden-attribute tripwire in tests/web/test_ui_contracts.py has teeth.

Re-runs the same logic against the stylesheets as they were *before* the UI review fixes
(everything after the ``UI review fixes`` marker removed) and reports what it would flag.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
MARKER = "UI review fixes"

HIDDEN_CLASSES = {"writing-result", "writing-loading", "kn-active-filters", "panel"}


def _text(path: Path, *, strip_fix: bool) -> str:
    text = path.read_text(encoding="utf-8")
    if strip_fix and MARKER in text:
        text = text.split(MARKER)[0]
    return text


def report(*, strip_fix: bool) -> list[str]:
    display: dict[str, set[str]] = {}
    overrides: set[str] = set()
    for path in sorted(STATIC.glob("*.css")):
        text = _text(path, strip_fix=strip_fix)
        for match in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", text):
            if "display:" in match.group(2):
                display.setdefault(match.group(1), set()).add(path.name)
        overrides.update(re.findall(r"\.([A-Za-z0-9_-]+)\[hidden\]", text))
    return sorted(HIDDEN_CLASSES & display.keys() - overrides)


before = report(strip_fix=True)
after = report(strip_fix=False)
print("修复前会被测试标记的类：", before)
print("修复后仍被标记的类：", after or "（无）")
assert before == ["kn-active-filters", "writing-result"], before
assert after == [], after
print("结论：该契约测试确实能抓到这个缺陷，且当前代码已通过。")
