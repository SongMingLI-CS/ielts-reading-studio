from __future__ import annotations

import datetime as dt

from app.vocabulary import (
    classify_rows,
    collect_vocabulary,
    difficulty_hint,
    form_families,
    pos_group,
    related_words,
)
from app.vocabulary.srs import MAX_BOX, due_date, interval_days, next_box, schedule


def test_pos_group_maps_common_notations():
    assert pos_group("n.") == "noun"
    assert pos_group("noun") == "noun"
    assert pos_group("v.") == "verb"
    assert pos_group("vt.") == "verb"
    assert pos_group("adj.") == "adjective"
    assert pos_group("adv") == "adverb"
    assert pos_group("") == "other"
    assert pos_group(None) == "other"
    assert pos_group("phrase") == "other"


def test_difficulty_hint_uses_length_and_suffix():
    assert difficulty_hint("flux")["level"] == "基础"
    assert difficulty_hint("infrastructure")["level"] == "高阶"
    assert difficulty_hint("conservation")["level"] == "高阶"
    assert difficulty_hint("municipal")["level"] == "进阶"


def test_form_families_groups_shared_stems():
    rows = [
        {"word": "conservation", "key": "conservation", "passages": ["A"]},
        {"word": "conserve", "key": "conserve", "passages": ["B"]},
        {"word": "municipal", "key": "municipal", "passages": ["C"]},
    ]

    families = form_families(rows)

    assert len(families) == 1
    assert {item["word"] for item in families[0]["words"]} == {"conservation", "conserve"}


def test_related_words_lists_same_family_before_same_passage():
    rows = [
        {"word": "conservation", "key": "conservation", "passages": ["A"]},
        {"word": "conserve", "key": "conserve", "passages": ["B"]},
        {"word": "scarcity", "key": "scarcity", "passages": ["A"]},
    ]

    related = related_words(rows[0], rows)

    assert {"word": "conserve", "why": "同根词"} in related
    assert {"word": "scarcity", "why": "同篇出现"} in related
    assert related[0]["why"] == "同根词"


def test_classify_rows_builds_every_grouping():
    rows = [
        {"word": "conservation", "key": "conservation", "part_of_speech": "n.", "passages": ["A", "B", "C"]},
        {"word": "conserve", "key": "conserve", "part_of_speech": "v.", "passages": ["A"]},
        {"word": "municipal", "key": "municipal", "part_of_speech": "", "passages": ["B", "A"]},
    ]

    grouped = classify_rows(rows, {"conserve": {"box": 2, "due_at": None, "seen": 3, "lapses": 1}})

    assert next(item["label"] for item in grouped["by_frequency"]) == "高频复现（≥3 篇）"
    assert len(grouped["by_frequency"][0]["rows"]) == 1
    assert {item["label"] for item in grouped["by_source"]} == {"A", "B", "C"}
    assert grouped["by_pos"]["noun"]["rows"][0]["word"] == "conservation"
    assert grouped["by_pos"]["other"]["rows"][0]["word"] == "municipal"
    assert grouped["families"][0]["stem"] == "conserv"
    assert rows[1]["review_box"] == 2 and rows[1]["seen"] == 3
    assert rows[0]["review_box"] == 0


def test_interval_grows_with_each_box():
    assert [interval_days(box) for box in range(1, MAX_BOX + 1)] == [1, 2, 4, 8, 32]
    assert next_box(1, "good") == 2
    assert next_box(3, "hard") == 3
    assert next_box(4, "again") == 1
    assert next_box(MAX_BOX, "good") == MAX_BOX


def test_schedule_tracks_seen_and_lapses():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    fresh = schedule(None, "good", now=now)
    assert fresh["box"] == 2 and fresh["seen"] == 1 and fresh["lapses"] == 0
    assert fresh["due_at"] == now + dt.timedelta(days=2)

    forgot = schedule({"box": 4, "seen": 5, "lapses": 1}, "again", now=now)
    assert forgot["box"] == 1 and forgot["seen"] == 6 and forgot["lapses"] == 2
    assert forgot["due_at"] == now + dt.timedelta(days=1)


def test_due_date_follows_the_outcome():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    assert due_date(1, now=now, outcome="good") == now + dt.timedelta(days=2)
    assert due_date(3, now=now, outcome="hard") == now + dt.timedelta(days=4)


def test_collect_vocabulary_merges_words_across_passages(client, web_service, vocabulary_unit):
    rows = collect_vocabulary(web_service)

    assert {row["word"] for row in rows} == {
        "conservation",
        "conserve",
        "sustainable",
        "municipal",
        "infrastructure",
        "scarcity",
    }
    assert all(row["passage_count"] == 1 for row in rows)
    assert all(row["status"] == "" for row in rows)


def test_home_page_shows_classification_and_stats(client, vocabulary_unit):
    page = client.get("/vocabulary?group=all&scope=all")

    assert page.status_code == 200
    assert "词汇记忆" in page.text
    assert "全部词条" in page.text and "在复习队列" in page.text
    assert "按词性" in page.text and "同根词族" in page.text
    assert "conservation" in page.text and "基础设施" in page.text
    # 第 1 盒的间隔是 1 天，页面把间隔写清楚
    assert "间隔 1 天" in page.text


def test_classification_tabs_render_each_grouping(client, vocabulary_unit):
    for group, marker in (
        ("pos", "名词"),
        ("frequency", "只出现 1 次"),
        ("source", "Managing Water"),
        ("level", "高阶"),
        ("family", "conserv"),
    ):
        page = client.get(f"/vocabulary?group={group}&scope=all")
        assert page.status_code == 200, group
        assert marker in page.text, group


def test_word_detail_shows_memory_hooks(client, vocabulary_unit):
    page = client.get("/vocabulary?focus=conservation&scope=all")

    assert page.status_code == 200
    assert "记忆联想" in page.text
    assert "同根词" in page.text
    assert "conserve" in page.text
    assert "water conservation" in page.text


def test_track_endpoint_puts_a_word_into_the_review_queue(client, web_service, vocabulary_unit):
    response = client.post(
        "/vocabulary/track",
        data={"word": "conservation", "action": "add", "scope": "saved"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    state = web_service.repository.get_vocabulary_review("conservation")
    assert state["box"] == 1 and state["seen"] == 0

    client.post("/vocabulary/track", data={"word": "conservation", "action": "remove"})
    assert web_service.repository.get_vocabulary_review("conservation") is None


def test_review_session_shows_a_card_with_four_options(client, web_service, vocabulary_unit):
    client.post("/vocabulary/track", data={"word": "conservation", "action": "add"})

    page = client.get("/vocabulary/review")
    assert page.status_code == 200
    assert "conservation" in page.text
    assert page.text.count('name="choice"') == 4
    assert "保护；节约" in page.text  # 正确答案在选项里


def test_answering_correctly_moves_the_card_up_one_box(client, web_service, vocabulary_unit):
    client.post("/vocabulary/track", data={"word": "conservation", "action": "add"})

    response = client.post(
        "/vocabulary/review/answer",
        data={"word": "conservation", "choice": "保护；节约"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "feedback=right" in response.headers["location"]
    state = web_service.repository.get_vocabulary_review("conservation")
    assert state["box"] == 2 and state["seen"] == 1
    # 第 2 盒要等 2 天，因此不在"今天到期"里了
    assert "conservation" not in client.get("/vocabulary/review").text


def test_answering_wrong_sends_the_card_back_to_box_one(client, web_service, vocabulary_unit):
    client.post("/vocabulary/track", data={"word": "scarcity", "action": "add"})
    client.post(
        "/vocabulary/review/answer",
        data={"word": "scarcity", "choice": "保护；节约"},
        follow_redirects=False,
    )

    state = web_service.repository.get_vocabulary_review("scarcity")
    assert state["box"] == 1 and state["lapses"] == 1 and state["seen"] == 1

    page = client.get("/vocabulary/review?feedback=wrong&answer=%E4%BF%9D%E6%8A%A4")
    assert "答错了" in page.text


def test_self_grading_and_reset(client, web_service, vocabulary_unit):
    client.post("/vocabulary/track", data={"word": "municipal", "action": "add"})

    graded = client.post(
        "/vocabulary/review/grade",
        data={"word": "municipal", "outcome": "hard"},
        follow_redirects=False,
    )
    assert graded.status_code == 303
    assert web_service.repository.get_vocabulary_review("municipal")["box"] == 1

    assert client.post(
        "/vocabulary/review/grade", data={"word": "municipal", "outcome": "nonsense"}
    ).status_code == 422

    reset = client.post("/vocabulary/review/reset", data={"word": "municipal"}, follow_redirects=False)
    assert reset.status_code == 303
    assert web_service.repository.get_vocabulary_review("municipal") is None


def test_review_session_handles_an_unknown_word(client, vocabulary_unit):
    assert (
        client.post("/vocabulary/review/answer", data={"word": "zzz", "choice": "x"}).status_code
        == 404
    )


def test_review_page_explains_an_empty_queue(client):
    page = client.get("/vocabulary/review?mode=all")

    assert page.status_code == 200
    assert "暂时没有到期的词" in page.text
    assert "去生词本" in page.text
