from __future__ import annotations


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
