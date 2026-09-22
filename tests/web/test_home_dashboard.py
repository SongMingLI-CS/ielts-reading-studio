"""首页必须回答"现在点哪里"：继续草稿、推荐下一篇、今天该复习什么。

每项断言都对应一个具体的可发现性缺陷：返工时找不到草稿、找不到该复习的题、
或者用一个虚构的进度数字糊弄用户。
"""

from __future__ import annotations

import datetime as dt

from tests.fixtures.agent_payloads import assessment_payload


def _question_numbers() -> list[int]:
    return [
        question["number"]
        for group in assessment_payload()["question_groups"]
        for question in group["questions"]
    ]


def _all_correct_except(number: int | None = None) -> dict[str, str]:
    return {
        str(item): ("wrong" if item == number else "water supply")
        for item in _question_numbers()
    }


def test_home_offers_the_draft_with_the_question_to_continue_from(
    client, web_service, completed_unit
) -> None:
    """返工时一眼看到"继续第 3 题"，而不是先猜草稿在哪。"""

    web_service.repository.save_practice_attempt(
        "draft-1",
        completed_unit.id,
        status="in_progress",
        payload={"answers": {"1": "water supply", "2": "water supply"}, "elapsed_seconds": 504},
    )

    page = client.get("/")
    assert page.status_code == 200
    assert f'href="/practice/{completed_unit.id}"' in page.text
    assert "继续第 3 题 →" in page.text
    assert "已答 2 / 12 题" in page.text
    assert "8:24" in page.text
    assert 'class="resume-card" data-kind="resume"' in page.text


def test_home_never_restarts_a_draft_that_still_has_answers(
    client, web_service, completed_unit
) -> None:
    """``?fresh=1`` 会丢掉已写好的答案，首页绝不能给草稿配这个链接。"""

    web_service.repository.save_practice_attempt(
        "draft-1",
        completed_unit.id,
        status="in_progress",
        payload={"answers": {"1": "water supply"}, "elapsed_seconds": 60},
    )

    page = client.get("/")
    assert f'href="/practice/{completed_unit.id}?fresh=1"' not in page.text
    assert "清空" not in page.text


def test_home_recommends_the_first_passage_that_was_never_attempted(
    client, web_service, completed_unit
) -> None:
    page = client.get("/")
    assert 'data-kind="start"' in page.text
    assert "开始练习 →" in page.text
    assert f'href="/practice/{completed_unit.id}"' in page.text
    assert completed_unit.package.passage.title in page.text


def test_home_offers_a_redo_only_when_every_passage_has_been_tried(
    client, web_service, completed_unit
) -> None:
    """全部篇目都做过：给出重做入口，并说清会清空草稿。"""

    web_service.repository.save_practice_attempt(
        "submitted-1",
        completed_unit.id,
        status="submitted",
        payload={"answers": _all_correct_except()},
        score=12,
        total=12,
    )

    page = client.get("/")
    assert 'data-kind="redo"' in page.text
    assert f'href="/practice/{completed_unit.id}?fresh=1"' in page.text
    assert "重新开始会清空这篇的草稿" in page.text


def test_home_review_counts_come_from_real_records(
    client, web_service, completed_unit, vocabulary_unit
) -> None:
    """错题 / 生词本 / 到期复习三个数字都必须是真实记录，且"到期"只看已排期的词。"""

    web_service.repository.save_practice_attempt(
        "submitted-1",
        completed_unit.id,
        status="submitted",
        payload={"answers": _all_correct_except(number=5)},
        score=11,
        total=12,
    )
    web_service.repository.set_vocabulary_mark("conservation", "saved")
    web_service.repository.set_vocabulary_mark("conserve", "saved")
    now = dt.datetime.now(dt.UTC)
    web_service.repository.upsert_vocabulary_review(
        "conservation", box=1, due_at=now - dt.timedelta(days=1), seen=1, lapses=0
    )
    # 还没到期：算进"在复习队列"，但不算今天的待办
    web_service.repository.upsert_vocabulary_review(
        "conserve", box=2, due_at=now + dt.timedelta(days=2), seen=1, lapses=0
    )

    page = client.get("/")
    assert "<span>错题复习</span><strong>1 题 →</strong>" in page.text
    assert "<span>生词本</span><strong>2 词 →</strong>" in page.text
    assert "<span>到期待复习</span><strong>1 词 →</strong>" in page.text


def test_home_shows_guidance_instead_of_invented_progress(client) -> None:
    """没有任何记录时不摆出一排 0，而是说清下一步做什么。"""

    page = client.get("/")
    assert 'data-kind="empty"' in page.text
    assert "先导入一份中文素材" in page.text
    assert 'href="/corpora/import"' in page.text
    assert "quick-row" not in page.text
    assert "完成一篇练习后" in page.text


def test_home_puts_the_next_step_above_the_tool_cards(
    client, web_service, completed_unit
) -> None:
    """评审 P1-04：学习入口必须在工具介绍之前，否则首屏又变成介绍页。"""

    page = client.get("/")
    assert page.text.index('class="next-step"') < page.text.index('class="tools"')
    assert page.text.index('class="tools"') < page.text.index('class="product-grid"')
    assert "浏览工具" in page.text
