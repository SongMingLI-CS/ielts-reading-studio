"""练习中心的搜索、进度筛选与"继续第 N 题"。"""

import re

from app.models import UnitStatus


def _question_count(unit) -> int:
    return sum(len(group.questions) for group in unit.package.question_groups)


def test_practice_library_searches_in_the_title(client, completed_unit) -> None:
    title = completed_unit.package.passage.title
    needle = title.split()[0]

    hits = client.get(f"/practice?q={needle}")
    assert hits.status_code == 200
    assert "搜索篇名</label>" in hits.text
    assert title in hits.text

    misses = client.get("/practice?q=zzzznotapassage")
    assert misses.status_code == 200
    assert "没有篇名包含「zzzznotapassage」的篇目" in misses.text
    # 搜索框与筛选条还在，用户能自己改回来
    assert 'id="library-q"' in misses.text
    assert "查看全部篇目" in misses.text
    assert title not in misses.text


def test_practice_library_filters_by_progress_and_counts(
    client, web_service, completed_unit
) -> None:
    before = client.get("/practice")
    assert "全部 1" in before.text
    assert "未开始 1" in before.text
    assert "已完成 0" in before.text

    fresh = client.get("/practice?state=new")
    assert completed_unit.package.passage.title in fresh.text
    done = client.get("/practice?state=done")
    assert "这个进度下还没有篇目" in done.text

    web_service.repository.transition(
        completed_unit.id, UnitStatus.COMPLETED, UnitStatus.COMPLETED
    )
    client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "done-1", "answers": {"1": "water"}},
    )

    after = client.get("/practice")
    assert "已完成 1" in after.text
    assert "未开始 0" in after.text
    assert "已完成 1 次" in after.text

    only_done = client.get("/practice?state=done")
    assert completed_unit.package.passage.title in only_done.text
    only_new = client.get("/practice?state=new")
    assert "这个进度下还没有篇目" in only_new.text


def test_practice_library_offers_continue_with_the_next_question(
    client, completed_unit
) -> None:
    total = _question_count(completed_unit)
    client.post(
        f"/practice/{completed_unit.id}/save",
        json={"attempt_id": "draft-1", "answers": {"1": "water"}, "elapsed_seconds": 30},
    )

    page = client.get("/practice")

    assert "继续第 2 题" in page.text
    assert f"进行中 1 / {total}" in page.text
    # 草稿链接不能带 ?fresh=1，那会清掉已写好的答案
    card = re.search(
        r'<article class="practice-book-card" data-status="in_progress".*?</article>',
        page.text,
        re.DOTALL,
    )
    assert card, "没有渲染进行中的卡片"
    assert f'href="/practice/{completed_unit.id}"' in card.group(0)
    assert "?fresh=1" not in card.group(0)


def test_practice_library_guides_when_every_passage_is_done(client, completed_unit) -> None:
    client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "all-done", "answers": {"1": "water"}},
    )

    page = client.get("/practice")

    assert "这些篇目都做完了" in page.text
    assert 'href="/practice/review?tab=mistakes"' in page.text
    assert 'href="/corpora"' in page.text


def test_practice_library_prefers_a_newer_draft_over_an_older_score(
    client, completed_unit
) -> None:
    """草稿比旧成绩新：按钮必须是"继续"，不能是带 ?fresh=1 的"重做"（那会清掉草稿）。"""

    client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "old-score", "answers": {"1": "water"}},
    )
    client.post(
        f"/practice/{completed_unit.id}/save",
        json={
            "attempt_id": "newer-draft",
            "answers": {"1": "water", "2": "keep"},
            "elapsed_seconds": 12,
        },
    )

    page = client.get("/practice")
    card = re.search(
        r'<article class="practice-book-card" data-status="(\w+)".*?</article>',
        page.text,
        re.DOTALL,
    )
    assert card, "没有渲染卡片"
    assert card.group(1) == "in_progress"
    assert "继续第 3 题" in card.group(0)
    assert "?fresh=1" not in card.group(0)
    assert "重做" not in card.group(0)
    # 这一篇算"进行中"，而作答次数仍单独统计
    assert "<strong>1</strong><span>进行中</span>" in page.text
    assert "<strong>1</strong><span>已提交</span>" in page.text


def test_practice_library_empty_state_points_at_the_corpus(client) -> None:
    page = client.get("/practice")

    assert "还没有可练习的篇目" in page.text
    assert "去语料库导入" in page.text
    assert 'href="/corpora"' in page.text
    # 没有篇目时不显示搜索框，避免搜一个不存在的库
    assert 'id="library-q"' not in page.text
