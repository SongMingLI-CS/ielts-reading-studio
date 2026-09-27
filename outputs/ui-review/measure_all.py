"""Measure the viewports the UI review cares about and print one line each."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = "55ca3b03-7f3b-5bed-985c-90a688fdceb3"

CASES = [
    ("手机 390 · 首页（有草稿）", "http://127.0.0.1:8765/", "390x844", ("resumeAction", "firstQuickRow")),
    ("手机 390 · 练习中心", "http://127.0.0.1:8765/practice", "390x844", ("firstCard",)),
    ("平板 768 · 练习页", f"http://127.0.0.1:8765/practice/{UNIT}", "768x1024", ("readingPane",)),
    ("平板 768 · 导航", "http://127.0.0.1:8765/", "768x1024", ("nav", "resumeAction")),
    ("桌面 1440 · 首页（有草稿）", "http://127.0.0.1:8765/", "1440x900", ("resumeAction", "toolsHeading", "productLink")),
    ("桌面 1440 · 写作页", "http://127.0.0.1:8765/writing", "1440x900", ("writingResult",)),
    ("桌面 1440 · 知识库", "http://127.0.0.1:8765/knowledge", "1440x900", ("nav",)),
    ("桌面 1440 · 练习页", f"http://127.0.0.1:8765/practice/{UNIT}", "1440x900", ("readingPane",)),
]

for label, url, size, keys in CASES:
    completed = subprocess.run(
        ["uv", "run", "python", str(ROOT / "outputs/ui-review/cdp_measure.py"), url, size],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        print(f"{label}: 量测失败 {completed.stderr.strip()[-200:]}")
        continue
    payload = json.loads(completed.stdout.split("screenshot", 1)[0])
    parts = []
    for key in keys:
        box = payload["boxes"][key]
        parts.append("无此元素" if box is None else f"{key} top={box['top']} 宽={box['width']}")
    print(
        f"{label}: 视口={payload['viewport']['width']} 文档宽={payload['scrollWidth']} "
        f"越界={len(payload['offenders'])} | " + " | ".join(parts)
    )
    print(f"    契约: {json.dumps(payload['checks'], ensure_ascii=False)}")
