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


def test_every_shell_ships_a_skip_link_and_a_main_target() -> None:
    """键盘用户不该按 13 次 Tab 才能到达内容：页头之前要有一个跳转链接。"""

    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    assert '<a class="skip-link" href="#main">' in base
    assert 'id="main"' in base
    # 跳转链接必须是 body 的第一个可聚焦元素
    assert base.index('class="skip-link"') < base.index('class="site-header"')


def test_stylesheets_load_in_token_accessibility_order() -> None:
    """tokens.css 在最前（供所有组件取色），accessibility.css 在最后（要盖过组件）。"""

    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    sheets = re.findall(r'href="/static/([a-z-]+\.css)', base)

    assert sheets[0] == "tokens.css"
    assert sheets[-1] == "accessibility.css"
    assert sheets.index("tokens.css") < sheets.index("app.css")


def test_palette_is_defined_once() -> None:
    """十四个近似灰正是"每个人都自己挑一个"的结果：调色板只能定义在 tokens.css。"""

    tokens = _normalized(STATIC / "tokens.css")
    for token in (
        "--ink:#172029",
        "--ink-secondary:#626d67",
        "--ink-muted:#5b6660",
        "--ink-decor:#7f8a84",
        "--success-ink:#1f7a58",
        "--warning-ink:#6f6039",
        "--focus-ring:#123c35",
        "--tap:44px",
    ):
        assert token in tokens, token

    # app.css 曾经自带一份重复定义，两处会悄悄漂移
    app = _normalized(STATIC / "app.css")
    for token in ("--ink:", "--forest:", "--green:", "--mint:", "--line:", "--orange:"):
        assert token not in app, f"{token} 仍定义在 app.css"


def test_audited_low_contrast_greys_are_gone() -> None:
    """这些字面量是实测低于 4.5:1 的灰色（最低 2.18:1），必须走令牌。"""

    legacy = (
        "#9aa4a0", "#9aa5a1", "#9aa7a1", "#8b9691", "#8b9591", "#8a958f", "#8a9590",
        "#7d8a85", "#7b8783", "#7b8681", "#78837e", "#76807c", "#6f7b76", "#708078",
        "#6d7a75", "#6d7973", "#6c7772", "#6b7771", "#69746f", "#66736e", "#a7b2ad",
        "#b0bab5", "#6b7772", "#a8b2ae", "#2d9b71", "#8a7a55",
    )
    for path in sorted(STATIC.glob("*.css")):
        if path.name == "print.css":  # 纸面调色板单独验证
            continue
        text = _normalized(path)
        for value in legacy:
            assert f"color:{value}" not in text, f"{path.name} 仍在使用 {value}"


def test_tokens_carry_the_measured_contrast_values() -> None:
    """令牌旁边写实测值，改色的人才知道下界在哪里。"""

    tokens = _normalized(STATIC / "tokens.css")

    assert "对比度按 WCAG 2.1" in tokens
    # 归一化后空白会被折叠成单个空格
    assert "--ink-secondary 4.87 / 5.38" in tokens
    assert "--ink-muted 5.09 / 5.62" in tokens
    # 装饰性大数字只需 3:1，其余小字必须 ≥4.5:1
    assert "装饰性大数字" in tokens
    assert "低于 4.5 的灰色一律不再新增" in tokens


def test_reduced_motion_and_focus_are_handled_centrally() -> None:
    """动效与焦点环必须在最后一层统一，否则每个组件都要各写一遍。"""

    a11y = _normalized(STATIC / "accessibility.css")

    assert "@media (prefers-reduced-motion:reduce)" in a11y
    assert "transition-duration:.001ms!important" in a11y
    assert "animation-duration:.001ms!important" in a11y
    assert "transform:none!important" in a11y
    # 焦点环：深浅底各一套，且在深色页头里换成浅色
    assert ":focus-visible" in a11y
    assert "outline:var(--focus-width) solid var(--focus-ring)" in a11y
    assert ".site-header a:focus-visible" in a11y


def test_touch_targets_use_the_shared_token() -> None:
    """44px 只能来自 --tap，散落的魔法数字会让下次改版重新漏掉控件。"""

    a11y = _normalized(STATIC / "accessibility.css")

    assert "--tap" in _normalized(STATIC / "tokens.css")
    assert ".skip-link,.bottom-nav a{min-height:var(--tap)}" in a11y
    assert "a[href]{min-height:24px}" in a11y
    assert "@media (max-width:900px){" in a11y
    assert "a[href]{min-height:var(--tap);min-width:var(--tap)}" in a11y
    assert ".practice-arrow,.button,button,input[type=\"submit\"]{min-height:var(--tap)}" in a11y
    # 段落里的行内链接按 WCAG 豁免，不能被撑成 44px 高的行内块
    assert "p .text-link,li .text-link{display:inline;min-width:0;min-height:0" in a11y


def test_danger_text_link_does_not_inherit_the_button_red() -> None:
    """<a class="text-link danger"> 曾经同时拿到按钮红底与深红字，实测 1.18:1。"""

    corpora = _normalized(STATIC / "corpora.css")

    assert ".text-link.danger{color:#a8431f;background:none}" in corpora


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
    """390×844 实测：概览格塌成 4 行时首篇练习卡要到 1232px；压成一行 4 格后是 697px。"""

    css = _normalized(STATIC / "practice-center.css")

    assert ".hero-actions{min-width:0;max-width:100%;flex-direction:row;flex-wrap:wrap" in css
    assert ".overview-grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:.35rem;margin-top:.4rem}" in css
    # 一列网格的子项必须允许收缩，否则内容的 min-content 会把整页顶出 390px 视口
    assert ".practice-hero,.hero-copy,.study-overview{min-width:0}" in css
    # 390px 是设计师实机宽度；任何让它退回单列的规则都会复活「首卡在 1232px」的缺陷
    assert ".overview-grid{grid-template-columns:1fr}" not in css
    # 手机上的 hero 说明只是占位：留着它，搜索框与筛选标签就压在第一屏之外
    assert ".hero-copy .lead{display:none}" in css


def test_mobile_library_puts_search_and_filters_above_the_first_card() -> None:
    """390 实测：搜索 587 / 筛选 641 / 首篇卡 697，三行都落在底栏 787 之上。"""

    css = _normalized(STATIC / "practice-center.css")

    assert "body:has(.practice-hero) .library-toolbar{display:none}" in css
    # 四个筛选标签在窄屏横向滚动，占一行而不是换行成两行
    assert ".filter-tabs{flex-wrap:nowrap;overflow-x:auto" in css
    # .library-search 的按钮保持 44px 触控高度
    assert ".library-search button{min-height:44px}" in css


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


def test_next_step_card_is_a_full_width_single_column_on_small_screens() -> None:
    """"继续练习 + 今天复习"是首页首屏的主体：窄屏必须竖排，不能挤成两列。"""

    css = _normalized(STATIC / "studio.css")

    assert ".next-step{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr)" in css
    assert ".next-step{grid-template-columns:minmax(0,1fr);gap:.8rem;margin-bottom:1.6rem}" in css
    assert ".quick-row{display:flex;align-items:center;justify-content:space-between" in css
    # 触控目标与全站一致：复习入口不能比 44px 更矮
    assert "min-height:44px" in css


def test_mobile_bottom_nav_adds_four_thumb_reachable_entries(client, completed_unit) -> None:
    """窄屏多一条底栏（首页/阅读/复习/语料）：顶部仍是全部入口，底栏只解决拇指够不到。"""

    page = client.get("/")
    block = re.search(r'<nav class="bottom-nav".*?</nav>', page.text, re.DOTALL)
    assert block, "页面没有渲染手机底栏"
    nav = block.group(0)
    assert nav.count("<a ") == 4
    for href, label in (
        ("/", "首页"),
        ("/practice", "阅读"),
        ("/practice/review", "复习"),
        ("/corpora", "语料"),
    ):
        assert f'href="{href}"' in nav, href
        assert f">{label}</a>" in nav, label
    assert 'href="/" aria-current="page"' in nav

    review = re.search(
        r'<nav class="bottom-nav".*?</nav>', client.get("/practice/review").text, re.DOTALL
    )
    assert review, "复习中心没有底栏"
    assert 'href="/practice/review" aria-current="page"' in review.group(0)
    assert 'href="/practice" aria-current="page"' not in review.group(0)

    # 答题页底部已经有提交栏，所以 CSS 里要靠它把底栏关掉
    assert 'class="submit-dock"' in client.get(f"/practice/{completed_unit.id}").text

    css = _normalized(STATIC / "app.css")
    assert ".bottom-nav{display:none}" in css
    assert ".bottom-nav{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))" in css
    assert "position:fixed;left:0;right:0;bottom:0;z-index:30" in css
    # 页面给底栏让出高度，内容不会被压住
    assert "body{padding-bottom:4.2rem}" in css
    # 答题页的两条底栏不能叠在一起
    assert "body:has(.submit-dock){padding-bottom:0}" in css
    assert "body:has(.submit-dock) .bottom-nav{display:none}" in css
    # 打印不打印导航
    assert "@media print{.bottom-nav{display:none}}" in css

