"""复习中心（/practice/review）：一个入口装下错题、生词本和词汇复习。"""

import re

from tests.web.conftest import bootstrap_csrf


def _submit_wrong_answer(client, unit, attempt_id: str = "wrong-1") -> None:
    client.post(
        f"/practice/{unit.id}/submit",
        json={"attempt_id": attempt_id, "answers": {"1": "definitely wrong"}},
    )


def _tab_counts(text: str) -> dict[str, str]:
    return dict(
        re.findall(r"tab=(\w+)\"[^>]*><span>[^<]+</span><strong>(\d+)</strong>", text)
    )


def test_review_hub_opens_on_mistakes_and_counts_every_tab(
    client, completed_unit, vocabulary_unit
) -> None:
    _submit_wrong_answer(client, completed_unit)
    client.post(
        "/practice/vocabulary/mark", data={"word": "conservation", "action": "save"}
    )

    page = client.get("/practice/review")

    assert page.status_code == 200
    assert "<h1>复习</h1>" in page.text
    # 三个标签的数量来自真实记录：错题按未答/答错累计，收藏词 1 个，到期词 0 个
    counts = _tab_counts(page.text)
    assert set(counts) == {"mistakes", "words", "due"}
    assert int(counts["mistakes"]) > 0
    assert counts == {"mistakes": counts["mistakes"], "words": "1", "due": "0"}
    # 默认停在错题，并且错题内容确实渲染出来了
    assert re.search(r'tab=mistakes"[^>]*aria-current="page"', page.text)
    assert "你的答案" in page.text and "原文证据" in page.text


def test_review_hub_tabs_render_each_list(client, completed_unit, vocabulary_unit) -> None:
    _submit_wrong_answer(client, completed_unit)

    words = client.get("/practice/review?tab=words")
    assert words.status_code == 200
    assert "conservation" in words.text and "保护；节约" in words.text
    assert re.search(r'tab=words"[^>]*aria-current="page"', words.text)

    empty_due = client.get("/practice/review?tab=due")
    assert empty_due.status_code == 200
    assert "暂时没有到期的词" in empty_due.text

    # 把一个词排进复习队列后，同一个标签直接出卡
    client.post("/vocabulary/track", data={"word": "conservation", "action": "add"})
    due = client.get("/practice/review?tab=due")
    assert "conservation" in due.text

    # 未知标签回落到默认的那一个，而不是空页
    fallback = client.get("/practice/review?tab=unknown")
    assert fallback.status_code == 200
    assert "你的答案" in fallback.text


def test_review_hub_keeps_filters_and_print_links_usable(client, completed_unit) -> None:
    """壳里的筛选要带回自己的标签；打印这类原样页面保持原地址。"""

    _submit_wrong_answer(client, completed_unit)

    shell = client.get("/practice/review?tab=mistakes")
    assert "/practice/review?tab=mistakes&amp;type=" in shell.text
    assert "/practice/mistakes?type=" not in shell.text
    assert "/practice/mistakes/print" in shell.text

    # 独立页面保持原来的前缀，书签与打印链路不受影响
    standalone = client.get("/practice/mistakes")
    assert "/practice/mistakes?type=" in standalone.text


def test_word_marks_and_grades_can_return_to_the_review_shell(client, vocabulary_unit) -> None:
    bootstrap_csrf(client)

    marked = client.post(
        "/practice/vocabulary/mark",
        data={
            "word": "conservation",
            "action": "save",
            "return_to": "/practice/review?tab=words",
        },
        follow_redirects=False,
    )
    assert marked.status_code == 303
    assert marked.headers["location"] == "/practice/review?tab=words"

    # 白名单之外的值一律忽略：这里不能变成开放重定向
    hostile = client.post(
        "/practice/vocabulary/mark",
        data={
            "word": "conservation",
            "action": "save",
            "return_to": "https://example.invalid/steal",
        },
        follow_redirects=False,
    )
    assert hostile.headers["location"] == "/practice/vocabulary"

    client.post("/vocabulary/track", data={"word": "conservation", "action": "add"})
    graded = client.post(
        "/vocabulary/review/grade",
        data={
            "word": "conservation",
            "outcome": "good",
            "mode": "all",
            "return_to": "/practice/review?tab=due",
        },
        follow_redirects=False,
    )
    assert graded.status_code == 303
    assert graded.headers["location"].startswith("/practice/review?tab=due&")
    assert "feedback=good" in graded.headers["location"]

    ignored = client.post(
        "/vocabulary/review/reset",
        data={"word": "conservation", "return_to": "//example.invalid"},
        follow_redirects=False,
    )
    assert ignored.headers["location"] == "/vocabulary/review?feedback=reset"


def test_standalone_review_pages_keep_working(client, completed_unit) -> None:
    """旧地址是书签：有了汇总入口不等于把它们拆掉。"""

    _submit_wrong_answer(client, completed_unit)

    for path in (
        "/practice/mistakes",
        "/practice/vocabulary",
        "/vocabulary/review",
        "/vocabulary",
    ):
        assert client.get(path).status_code == 200, path


def test_navigation_and_practice_lead_to_the_single_review_entry(
    client, completed_unit, vocabulary_unit
) -> None:
    # 有真实记录时首页才显示"今天可以复习"，这里制造一条错题
    _submit_wrong_answer(client, completed_unit)

    home = client.get("/")

    assert '<a href="/practice/review"' in home.text
    for tab in ("mistakes", "words", "due"):
        assert f'href="/practice/review?tab={tab}"' in home.text
    # 导航里不再并列"词汇"这种近似入口
    assert '<a href="/vocabulary"' not in home.text

    practice = client.get("/practice")
    assert 'href="/practice/review?tab=mistakes"' in practice.text
    assert 'href="/practice/review?tab=words"' in practice.text
