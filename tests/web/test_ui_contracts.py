"""Contracts that keep the UI working under the CSP and at every viewport width.

Every test here encodes a defect that reached production:

* an inline event handler that the content security policy silently blocked (the print
  buttons and two destructive chapter-editing forms did nothing);
* a ``hidden`` attribute that lost to a component's own ``display`` declaration, so an
  empty result area showed before anything was evaluated;
* layout rules that only held at one width, so the tablet navigation wrapped per character
  and the visible practice pane stayed at 53% with half the screen left blank.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

INLINE_HANDLER = re.compile(r"\son(?:click|change|submit|input|load|error|focus|blur)\s*=")

#: Tags whose ``hidden``/``type="hidden"`` is not the boolean display attribute.
_NOT_THE_ATTRIBUTE = (
    re.compile(r'aria-hidden="[^"]*"'),
    re.compile(r'type="hidden"'),
)


def _normalized(path: Path) -> str:
    """Stylesheet text with whitespace runs collapsed, so assertions survive reformatting.

    Runs are collapsed to a single space instead of being removed: spaces inside selectors
    are significant (``nav a`` is not ``nava``).
    """

    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _hidden_classes() -> set[str]:
    """Classes of elements hidden through the boolean ``hidden`` attribute."""

    found: set[str] = set()
    for path in sorted(TEMPLATES.rglob("*.html")):
        for tag in re.finditer(r"<[a-z][^>]*>", path.read_text(encoding="utf-8")):
            element = tag.group(0)
            for pattern in _NOT_THE_ATTRIBUTE:
                element = pattern.sub("", element)
            if not re.search(r"\shidden(?=[\s>])", element):
                continue
            for attribute in re.findall(r'class="([^"]+)"', element):
                found.update(attribute.split())
    return found


def test_no_template_relies_on_inline_event_handlers() -> None:
    """``script-src 'self' 'nonce-…'`` blocks inline handlers without any visible error."""

    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if INLINE_HANDLER.search(line)
    ]

    assert not offenders, (
        "内联事件处理器会被 CSP 静默拦掉（script-src 只有 'self' 与 nonce）："
        + "、".join(offenders)
    )


def test_script_policy_still_forbids_inline_and_eval(client) -> None:
    """The policy above is what makes the previous test necessary; keep both in sync."""

    policy = client.get("/practice/mistakes/print").headers["content-security-policy"]
    script_src = next(part for part in policy.split("; ") if part.startswith("script-src"))

    assert "'self'" in script_src
    assert "'nonce-" in script_src
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_print_pages_trigger_printing_from_an_external_script(client) -> None:
    for url in ("/practice/mistakes/print", "/practice/vocabulary/print", "/knowledge/print"):
        page = client.get(url)
        assert page.status_code == 200
        assert "打印 / 存为 PDF" in page.text
        assert 'data-print-trigger' in page.text
        assert "/static/print.js" in page.text
        assert INLINE_HANDLER.search(page.text) is None

    script = client.get("/static/print.js")
    assert script.status_code == 200
    assert "window.print()" in script.text


def test_destructive_chapter_forms_confirm_through_the_external_script(client, web_service, sample_txt) -> None:
    """The confirmations must not depend on an inline handler the browser refuses to run."""

    manifest = web_service.import_source(sample_txt)
    page = client.get(f"/corpora/{manifest.corpus.id}/preview")

    assert page.status_code == 200
    assert "onsubmit=" not in page.text
    assert "data-confirm=" in page.text
    assert "data-auto-submit" in page.text
    assert "/static/corpora.js" in page.text

    script = client.get("/static/corpora.js")
    assert script.status_code == 200
    assert "window.confirm(message)" in script.text
    assert "[data-auto-submit]" in script.text


def test_hidden_attribute_wins_over_component_display() -> None:
    """A component that sets ``display`` must also carry a ``[hidden]`` override."""

    display_classes: dict[str, set[str]] = {}
    overrides: set[str] = set()
    for path in sorted(STATIC.glob("*.css")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", text):
            if "display:" in match.group(2):
                display_classes.setdefault(match.group(1), set()).add(path.name)
        overrides.update(re.findall(r"\.([A-Za-z0-9_-]+)\[hidden\]", text))

    failures = [
        f".{name}（{', '.join(sorted(display_classes[name]))}）"
        for name in sorted(_hidden_classes() & display_classes.keys())
        if name not in overrides
    ]

    assert not failures, "hidden 会被组件自己的 display 覆盖，需要 [hidden] 兜底：" + "、".join(failures)


def test_tablet_navigation_wraps_between_links_not_inside_them() -> None:
    css = _normalized(STATIC / "app.css")

    assert "(min-width:701px) and (max-width:1280px)" in css
    assert ".site-header nav a{white-space:nowrap}" in css
    assert ".site-header nav{flex-wrap:wrap;justify-content:flex-end" in css


def test_mobile_tabs_collapse_the_practice_grid_to_one_column() -> None:
    css = _normalized(STATIC / "app.css")

    assert '.practice-shell.mobile-tabs-enabled[data-mobile-pane="reading"] .answer-pane{display:none}' in css
    assert ".practice-shell.mobile-tabs-enabled{grid-template-columns:minmax(0,1fr)}" in css


def test_mobile_library_keeps_stats_on_one_row() -> None:
    """390×844 实测：概览格塌成 4 行时首篇练习卡要到 1232px；压成一行 4 格后是 771px。"""

    css = _normalized(STATIC / "practice-center.css")

    assert ".hero-actions{min-width:0;max-width:100%;flex-direction:row;flex-wrap:wrap" in css
    assert ".overview-grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:.35rem;margin-top:.55rem}" in css
    # 一列网格的子项必须允许收缩，否则内容的 min-content 会把整页顶出 390px 视口
    assert ".practice-hero,.hero-copy,.study-overview{min-width:0}" in css
    # 390px 是设计师实机宽度；任何让它退回单列的规则都会复活「首卡在 1232px」的缺陷
    assert ".overview-grid{grid-template-columns:1fr}" not in css


def test_mobile_home_hero_fits_the_first_screen() -> None:
    """390×844 实测：hero 与产品卡各收一层留白，首屏才放得下"下一步"卡片。"""

    css = _normalized(STATIC / "studio.css")
    mobile = css.split("@media (max-width:600px){", 1)[-1]

    assert "body:has(.studio-hero) main{padding-top:.8rem}" in mobile
    assert ".studio-hero .lead{font-size:.95rem;line-height:1.55}" in mobile
    # 竖排的大数字块自己就占 100px 首屏高度，手机上必须横排
    assert ".studio-total{flex-direction:row;align-items:baseline;flex-wrap:wrap" in mobile
    assert ".product-card{min-height:0;padding:1.2rem}" in mobile


def test_navigation_is_grouped_with_a_label_per_group(client) -> None:
    """12 个一级链接平铺时，用户得先理解"系统有哪些模块"；分组后每组说清自己是什么。"""

    page = client.get("/")

    assert page.text.count('class="nav-group"') == 3
    for label in ("学习", "内容管理", "账户"):
        assert f'<span class="nav-label">{label}</span>' in page.text
    # 任务页此前没有任何入链，导航里必须有入口
    assert '<a href="/jobs"' in page.text

    css = _normalized(STATIC / "app.css")
    assert ".site-header .nav-group{display:flex;align-items:center;gap:1.35rem}" in css
    # 手机端不再是"隐藏滚动条的横滚导航"：分组换行，没有藏起来的入口
    assert ".site-header nav{display:block;overflow:visible;flex-wrap:wrap" in css
    assert ".site-header .nav-label{display:block;flex:0 0 auto;margin-right:.15rem" in css
    # 长标签在窄屏换短名，桌面保留全称（display:none 的那份不会进无障碍树）
    assert ".site-header .nav-short{display:none}" in css
    assert ".site-header .nav-full{display:none}" in css
    assert '<span class="nav-full">阅读练习</span><span class="nav-short">阅读</span>' in page.text
    """"继续练习 + 今天复习"是首页首屏的主体：窄屏必须竖排，不能挤成两列。"""

    css = _normalized(STATIC / "studio.css")

    assert ".next-step{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr)" in css
    assert ".next-step{grid-template-columns:minmax(0,1fr);gap:.8rem;margin-bottom:1.6rem}" in css
    assert ".quick-row{display:flex;align-items:center;justify-content:space-between" in css
    # 触控目标与全站一致：复习入口不能比 44px 更矮
    assert "min-height:44px" in css
