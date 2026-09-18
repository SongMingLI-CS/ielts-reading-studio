from __future__ import annotations

import json

import pytest

NOVEL_GLOSSARY = [
    {
        "word": "abandon",
        "lemma": "abandon",
        "meaning": "丢下",
        "part_of_speech": "verb",
        "cefr": "B2",
        "phonetic": "/əˈbændən/",
        "collocation": "abandon ship",
        "example_sentence": "They had to abandon their lands.",
        "first_chapter": 1,
        "last_chapter": 4,
        "occurrence_count": 3,
    },
    {
        "word": "a hidden world",
        "lemma": "a hidden world",
        "meaning": "别有洞天",
        "part_of_speech": "noun phrase",
        "cefr": "C1",
        "phonetic": "/ə ˈhɪdn wɜːld/",
        "collocation": "reveal a hidden world",
        "example_sentence": "The cave opened into a hidden world.",
        "first_chapter": 17,
        "last_chapter": 17,
        "occurrence_count": 1,
    },
]


@pytest.fixture
def novel_output(web_service):
    root = web_service.config.output_dir / "context-novel"
    (root / "chapter_json").mkdir(parents=True, exist_ok=True)
    (root / "glossary.json").write_text(
        json.dumps(NOVEL_GLOSSARY, ensure_ascii=False), encoding="utf-8"
    )
    (root / "index_entries.json").write_text(
        json.dumps([[1, "血尸"], [17, "别有洞天"]], ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_terms_view_lists_words_from_both_sources(
    client, vocabulary_unit, novel_output
):
    page = client.get("/knowledge")

    assert page.status_code == 200
    assert "知识点汇总" in page.text
    # 阅读侧的词与小说侧的词都在同一份手册里
    assert "conservation" in page.text
    assert "abandon" in page.text
    assert "固定搭配" in page.text
    assert "情境小说 · 第 1 章 血尸" in page.text
    assert "阅读练习 ·" in page.text
    # 例句里高亮了被讲解的词
    assert 'class="kw"' in page.text
    assert "/static/knowledge.css?v=1" in page.text


def test_collocations_view_lists_phrases(client, vocabulary_unit, novel_output):
    page = client.get("/knowledge?view=collocations")

    assert page.status_code == 200
    # 搭配卡片里把核心词标出来，所以短语会被 <mark> 切开
    assert '<mark class="kw-coll">abandon</mark> ship' in page.text
    assert "reveal <mark" in page.text
    assert 'water <mark class="kw-coll">conservation</mark>' in page.text
    assert "所属词" in page.text


def test_paraphrase_view_pairs_stem_with_evidence(client, vocabulary_unit):
    page = client.get("/knowledge?view=paraphrase")

    assert page.status_code == 200
    assert "题干" in page.text
    assert "原文 ·" in page.text
    assert 'class="kw-sub"' in page.text or "重点线索" in page.text


def test_source_and_text_filters_narrow_the_list(client, vocabulary_unit, novel_output):
    only_novel = client.get("/knowledge?source=novel")
    assert "abandon" in only_novel.text
    assert "conservation" not in only_novel.text

    search = client.get("/knowledge?q=别有洞天")
    assert "a hidden world" in search.text
    assert "abandon" not in search.text

    level = client.get("/knowledge?level=C1")
    assert "a hidden world" in level.text
    assert "abandon" not in level.text


def test_pagination_and_view_switch_keep_filters(client, vocabulary_unit, novel_output):
    page = client.get("/knowledge?size=1&sort=alpha")

    assert "第 1 / " in page.text
    assert "下一页" in page.text

    last = client.get("/knowledge?size=1&page=99&sort=alpha")
    assert last.status_code == 200

    switched = client.get("/knowledge?source=novel&view=collocations")
    assert switched.status_code == 200
    assert '<mark class="kw-coll">abandon</mark> ship' in switched.text


def test_missing_novel_data_shows_a_note_not_an_error(client, vocabulary_unit):
    page = client.get("/knowledge")

    assert page.status_code == 200
    assert "还没有读到情境小说的术语库" in page.text
    assert "conservation" in page.text


def test_empty_digest_renders_an_explanation(client):
    page = client.get("/knowledge")

    assert page.status_code == 200
    assert "这个筛选条件下没有知识点" in page.text


def test_markdown_export_contains_the_current_rows(
    client, vocabulary_unit, novel_output
):
    response = client.get("/knowledge/export.md?view=terms&sort=alpha")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert "# 知识点汇总" in response.text
    assert "### abandon" in response.text
    assert "- 固定搭配：abandon ship" in response.text


def test_print_page_drops_site_chrome(client, vocabulary_unit, novel_output):
    page = client.get("/knowledge/print?view=terms")

    assert page.status_code == 200
    assert "/static/print.css" in page.text
    assert "site-header" not in page.text
    assert "abandon" in page.text


def test_nav_links_to_the_knowledge_digest(client):
    page = client.get("/")

    assert 'href="/knowledge"' in page.text
