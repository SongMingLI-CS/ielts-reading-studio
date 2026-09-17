from __future__ import annotations

import json


def _novel_bytes() -> bytes:
    return (
        "第一章 起程\n" + "这是第一章的故事内容。" * 80 + "\n"
        "第二章 深夜\n" + "这是第二章的故事内容。" * 80
    ).encode()


def test_dashboard_unifies_both_learning_products(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "IELTS 阅读练习" in response.text
    assert "雅思词汇情境小说" in response.text
    assert 'href="/practice"' in response.text
    assert 'href="/novel"' in response.text


def test_novel_workspace_imports_and_estimates_without_api(client):
    imported = client.post(
        "/novel/import",
        files={"source": ("story.txt", _novel_bytes(), "text/plain")},
        follow_redirects=False,
    )

    assert imported.status_code == 303
    page = client.get("/novel")
    assert page.status_code == 200
    assert "story.txt" in page.text
    assert "2 章" in page.text
    assert "边界可信" in page.text
    assert "批量生成与恢复" in page.text
    assert "批量生成（最多 20 章）" in page.text

    estimate = client.post("/novel/estimate", data={"start": 1, "end": 2})
    assert estimate.status_code == 200
    assert "Token" in estimate.text
    assert "2" in estimate.text


def test_novel_generation_requires_api_key(client):
    client.post(
        "/novel/import",
        files={"source": ("story.txt", _novel_bytes(), "text/plain")},
    )

    response = client.post(
        "/novel/generate-sample",
        data={"chapter": 1, "confirmation": "确认生成样章"},
    )

    assert response.status_code == 409
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


def test_novel_health_reports_integrated_catalog(client):
    response = client.get("/novel/health")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["catalog_entries"] >= 5000


def test_completed_chapters_use_titles_and_offer_inline_preview(client, web_service):
    output = web_service.config.output_dir / "context-novel"
    (output / "html").mkdir(parents=True)
    (output / "chapters").mkdir(parents=True)
    (output / "html" / "第0001章.html").write_text(
        "<html><title>第一章 血尸</title><body>chapter</body></html>",
        encoding="utf-8",
    )
    (output / "chapters" / "第0001章.docx").write_bytes(b"docx")
    (output / "index_entries.json").write_text(
        json.dumps([[1, "第一章 血尸"]], ensure_ascii=False),
        encoding="utf-8",
    )

    page = client.get("/novel")
    preview = client.get("/novel/preview/1")
    download = client.get("/novel/download/1/docx")

    assert "0001" in page.text
    assert "第一章 血尸" in page.text
    assert 'href="/novel/preview/1"' in page.text
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert "attachment" not in preview.headers.get("content-disposition", "")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "0001_" in download.headers["content-disposition"]
