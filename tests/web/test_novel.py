from __future__ import annotations

import hashlib
import json
import sqlite3

from app.web import novel_library as library


def _novel_bytes() -> bytes:
    return (
        "第一章 起程\n" + "这是第一章的故事内容。" * 80 + "\n"
        "第二章 深夜\n" + "这是第二章的故事内容。" * 80
    ).encode()


def _import(client, name: str, body: bytes | None = None):
    return client.post(
        "/novel/import",
        files={"source": (name, body if body is not None else _novel_bytes(), "text/plain")},
        follow_redirects=False,
    )


def _write_chapters(web_service, book_id: str, count: int, *, has_docx: int = 0) -> None:
    """给某本书伪造 count 章成品，用来验证按书查看与分页。"""

    entry = library.book_entry(web_service, book_id)
    paths = library.book_paths(web_service, book_id, entry["extension"])
    (paths.output / "html").mkdir(parents=True, exist_ok=True)
    (paths.output / "chapters").mkdir(parents=True, exist_ok=True)
    entries = []
    for index in range(1, count + 1):
        title = f"第{index:04d}章 测试章节 {index}"
        (paths.output / "html" / f"第{index:04d}章.html").write_text(
            f"<html><title>{title}</title><body>chapter {index}</body></html>",
            encoding="utf-8",
        )
        if index <= has_docx:
            (paths.output / "chapters" / f"第{index:04d}章.docx").write_bytes(b"docx")
        entries.append([index, title])
    (paths.output / "index_entries.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


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
    _import(client, "story.txt")
    book_id = library.active_book_id(web_service)
    _write_chapters(web_service, book_id, 1)

    page = client.get(f"/novel?book={book_id}")
    preview = client.get(f"/novel/preview/1?book={book_id}")
    download = client.get(f"/novel/download/1/docx?book={book_id}")

    assert "0001" in page.text
    assert "第0001章 测试章节 1" in page.text
    assert f'href="/novel/preview/1?book={book_id}"' in page.text
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert "attachment" not in preview.headers.get("content-disposition", "")
    # 预览要能命中这本书；换成别的书（不存在）就不该给出文件
    assert client.get("/novel/preview/1?book=ffffffffffff").status_code == 404
    assert download.status_code == 404  # 这份 fixture 只写了 html，没有 docx


def _second_novel_bytes() -> bytes:
    return (
        "第一章 出发\n" + "这是另一本书的第一章。" * 80 + "\n"
        "第二章 到达\n" + "这是另一本书的第二章。" * 80 + "\n"
        "第三章 返程\n" + "这是另一本书的第三章。" * 80
    ).encode()


def test_every_uploaded_novel_stays_in_the_library(client, web_service):
    """导入第二本不会覆盖第一本：两本书各自保留自己的成品。"""

    _import(client, "first.txt")
    first = library.active_book_id(web_service)
    _write_chapters(web_service, first, 2, has_docx=1)

    _import(client, "second.txt", _second_novel_bytes())
    second = library.active_book_id(web_service)

    assert second != first
    page = client.get("/novel")
    assert "first.txt" in page.text
    assert "second.txt" in page.text
    assert set(library.read_index(web_service)["books"]) == {first, second}

    kept = client.get(f"/novel?book={first}")
    assert "第0001章 测试章节 1" in kept.text
    fresh = client.get(f"/novel?book={second}")
    assert "第0001章 测试章节 1" not in fresh.text
    assert "这本书还没有可阅读的章节" in fresh.text

    # 源文件各留一份；重复上传同一本会命中同一本书
    entry = library.book_entry(web_service, first)
    assert library.book_paths(web_service, first, entry["extension"]).source.is_file()
    again = _import(client, "first-again.txt")
    assert again.headers["location"] == f"/novel?book={first}"


def test_selecting_a_book_scopes_the_forms_and_the_estimate(client, web_service):
    _import(client, "first.txt")  # 2 章
    first = library.active_book_id(web_service)
    _import(client, "second.txt", _second_novel_bytes())  # 3 章
    second = library.active_book_id(web_service)

    page = client.get(f"/novel?book={first}")
    assert f'<input type="hidden" name="book" value="{first}">' in page.text
    assert "生成到《first》" in page.text

    switched = client.post("/novel/select", data={"book": first}, follow_redirects=False)
    assert switched.status_code == 303
    assert switched.headers["location"] == f"/novel?book={first}"
    assert library.active_book_id(web_service) == first

    # 估算跟着所选书走：第一本只有 2 章
    assert client.post("/novel/estimate", data={"book": first, "start": 1, "end": 2}).status_code == 200
    assert client.post("/novel/estimate", data={"book": first, "start": 1, "end": 3}).status_code == 422
    assert client.post("/novel/estimate", data={"book": second, "start": 1, "end": 3}).status_code == 200
    assert client.post("/novel/select", data={"book": "ffffffffffff"}).status_code == 404


def test_chapter_pagination_keeps_the_book_and_clamps_the_page(client, web_service):
    _import(client, "many.txt")
    book = library.active_book_id(web_service)
    _write_chapters(web_service, book, 25)

    first_page = client.get(f"/novel?book={book}")
    assert "共 25 章 · 每页 20 章 · 第 1 / 2 页" in first_page.text
    assert "第0020章 测试章节 20" in first_page.text
    assert "第0021章 测试章节 21" not in first_page.text
    assert f'href="/novel?book={book}&amp;page=2"' in first_page.text
    assert "← 上一页" not in first_page.text
    assert 'aria-current="page">1</span>' in first_page.text

    second_page = client.get(f"/novel?book={book}&page=2")
    assert "第 2 / 2 页" in second_page.text
    assert "第0021章 测试章节 21" in second_page.text
    assert "第0001章 测试章节 1" not in second_page.text
    assert "下一页 →" not in second_page.text
    assert f'href="/novel?book={book}&amp;page=1"' in second_page.text

    # 页码越界钳到最后一页，不会出现空页
    clamped = client.get(f"/novel?book={book}&page=99")
    assert "第 2 / 2 页" in clamped.text


def test_page_explains_why_chapters_failed_not_just_how_many(client, web_service):
    """「失败 3 章」要给出原因、章节号与下一步，否则操作者只能猜。"""

    _import(client, "story.txt")
    book = library.active_book_id(web_service)
    entry = library.book_entry(web_service, book)
    paths = library.book_paths(web_service, book, entry["extension"])
    paths.output.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(paths.output / "state.sqlite3")
    database.execute(
        """CREATE TABLE chapter_progress (
            chapter_id INTEGER PRIMARY KEY, status TEXT NOT NULL,
            inserted_count INTEGER NOT NULL DEFAULT 0, error_type TEXT, updated_at TEXT NOT NULL
        )"""
    )
    database.executemany(
        "INSERT INTO chapter_progress VALUES (?,?,0,?,'2026-09-22T08:24:00+00:00')",
        [
            (1, "completed", None),
            (2, "completed", None),
            (3, "failed", "billing"),
            (6, "failed", "billing"),
            (7, "failed", "ChapterConversionError"),
            (9, "pending", None),
        ],
    )
    database.commit()
    database.close()
    paths.run.write_text(
        json.dumps(
            {
                "status": "failed",
                "outcome": "failed",
                "description": "《story》失败章节重试",
                "chapters_completed": 0,
                "chapters_failed": 3,
                "failure_reason": "第 3 章失败：billing（连续失败 5）",
                "failure_hint": "模型账户余额或配额不足",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = client.get(f"/novel?book={book}")

    # 一个说明块：中文原因 + 内部标识 + 章节号 + 只给一次下一步
    assert "这 3 章没生成出来，原因如下" in page.text
    assert "模型账户余额或配额不足" in page.text
    assert "第 3、6 章" in page.text
    assert "这一章的正文转换失败" in page.text and "第 7 章" in page.text
    assert "下一步：" in page.text
    assert page.text.count("充值或换一个可用的密钥后点「重试失败章节」") == 1
    # 最近一次运行写清完成/失败，而不是笼统的「没有产出完整章节」
    assert "最近一次运行：《story》失败章节重试 · 完成 0 章、失败 3 章" in page.text
    # 被中断（崩溃恢复）的章节也要露出来，否则统计加起来对不上进度库的行数
    assert "<strong>1</strong>待重试" in page.text


def test_failed_run_surfaces_the_reason_instead_of_looking_successful(client, web_service):
    _import(client, "story.txt")
    book = library.active_book_id(web_service)
    entry = library.book_entry(web_service, book)
    paths = library.book_paths(web_service, book, entry["extension"])
    paths.run.write_text(
        json.dumps(
            {
                "status": "completed",
                "outcome": "no_chapters",
                "description": "《story》第 1 章样章",
                "chapters_completed": 0,
                "chapters_failed": 0,
                "failure_reason": "ERROR: 分块合并后的章节质量检查失败：density_too_low",
                "failure_hint": "这一章的词汇密度没达到下限（每 500 字至少 20 个词条）",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = client.get(f"/novel?book={book}")

    assert "进程正常结束，但本次没有章节通过质量检查" in page.text
    assert "density_too_low" in page.text
    assert "每 500 字至少 20 个词条" in page.text


def test_legacy_single_directory_migrates_into_its_own_book(client, web_service):
    """旧布局（所有书共用一个目录）会被归入各自的书，原文件只增不删。"""

    input_root = web_service.config.input_dir / "context-novel"
    legacy_output = web_service.config.output_dir / "context-novel"
    story = _novel_bytes()
    digest = hashlib.sha256(story).hexdigest()
    upload = input_root / "uploads" / digest / "source.txt"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(story)
    (legacy_output / "html").mkdir(parents=True, exist_ok=True)
    (legacy_output / "html" / "第0001章.html").write_text(
        "<html><title>第一章 起程</title><body>legacy</body></html>", encoding="utf-8"
    )
    (legacy_output / "index_entries.json").write_text(
        json.dumps([[1, "第一章 起程"]], ensure_ascii=False), encoding="utf-8"
    )
    (legacy_output / "active_source.json").write_text(
        json.dumps(
            {
                "name": "旧书.txt",
                "sha256": digest,
                "chapters": 2,
                "confident": True,
                "bytes": len(story),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = client.get("/novel")
    book = digest[:12]

    assert f"?book={book}" in page.text
    assert "旧书.txt" in page.text
    # 旧成品被归到这本书下面，仍然可以阅读
    assert "第一章 起程" in client.get(f"/novel?book={book}").text
    log = json.loads((legacy_output / "migration_log.json").read_text(encoding="utf-8"))
    assert log["attributed_to"]["id"] == book
    assert log["attributed_to"]["matched_chapters"] == 1
    # 只增不删：旧目录里的原件还在
    assert (legacy_output / "html" / "第0001章.html").is_file()
