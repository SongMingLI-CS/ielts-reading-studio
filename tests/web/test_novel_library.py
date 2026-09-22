"""书库层：源文件长期保留、按 sha256 去重、旧布局迁移只增不删。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from app.web import novel_library as library


def _register(service, body: bytes, filename: str = "book.txt") -> dict:
    source = service.config.base_dir / f"upload-{hashlib.sha256(body).hexdigest()[:8]}-{filename}"
    source.write_bytes(body)
    return library.register_book(
        service,
        digest=hashlib.sha256(body).hexdigest(),
        filename=filename,
        extension=Path(filename).suffix,
        payload_path=source,
        chapters=2,
        confident=True,
        bytes_written=len(body),
    )


def test_source_files_and_finished_chapters_live_under_the_book_id(web_service):
    entry = _register(web_service, "第一章 起程\n正文".encode(), "诡秘之主.txt")
    paths = library.book_paths(web_service, entry["id"], ".txt")

    assert paths.source == library.library_root(web_service) / entry["id"] / "source.txt"
    assert paths.output == library.books_root(web_service) / entry["id"]
    assert paths.run == paths.output / "run_status.json"
    assert paths.source.is_file()
    assert entry["name"] == "诡秘之主"
    assert entry["filename"] == "诡秘之主.txt"


def test_reimporting_the_same_file_reuses_the_entry_and_keeps_the_import_time(web_service):
    first = _register(web_service, "第一章 起程\n正文".encode(), "旧名字.txt")
    again = _register(web_service, "第一章 起程\n正文".encode(), "新名字.txt")

    index = library.read_index(web_service)
    assert list(index["books"]) == [first["id"]]
    assert index["active"] == first["id"]
    assert again["imported_at"] == first["imported_at"]  # 第一次导入的时间不被改写
    assert again["filename"] == "新名字.txt"
    assert library.book_paths(web_service, first["id"], ".txt").source.read_bytes() == (
        "第一章 起程\n正文".encode()
    )


def test_index_survives_a_corrupt_file(web_service):
    index_path = library.index_path(web_service)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("{ not json", encoding="utf-8")

    assert library.read_index(web_service) == {"active": None, "books": {}}
    assert library.resolve_book(web_service) is None


def test_resolution_prefers_explicit_then_active_then_newest(web_service):
    older = _register(web_service, "第一章 甲\n正文".encode(), "甲.txt")
    newer = _register(web_service, "第一章 乙\n正文".encode(), "乙.txt")

    assert older["id"] != newer["id"]
    assert library.resolve_book(web_service)["id"] == newer["id"]  # 最新导入的是当前书
    assert library.resolve_book(web_service, older["id"])["id"] == older["id"]
    assert library.resolve_book(web_service, "ffffffffffff")["id"] == newer["id"]
    library.set_active_book(web_service, older["id"])
    assert library.resolve_book(web_service)["id"] == older["id"]


def test_listing_marks_the_current_book_and_counts_finished_chapters(web_service):
    first = _register(web_service, "第一章 甲\n正文".encode(), "甲.txt")
    second = _register(web_service, "第一章 乙\n正文".encode(), "乙.txt")
    output = library.book_paths(web_service, second["id"], ".txt").output
    (output / "html").mkdir(parents=True)
    (output / "html" / "第0001章.html").write_text("<html>ok</html>", encoding="utf-8")
    (output / "index_entries.json").write_text(
        json.dumps([[1, "第一章 乙"]], ensure_ascii=False), encoding="utf-8"
    )

    rows = library.list_books(web_service)

    assert [row["id"] for row in rows] == [second["id"], first["id"]]  # 当前书排最前
    assert rows[0]["active"] is True and rows[0]["generated"] == 1
    assert rows[0]["has_output"] is True and rows[0]["source_exists"] is True
    assert rows[1]["active"] is False and rows[1]["generated"] == 0



def test_migration_attributes_old_output_when_the_titles_match(web_service):
    """旧成品与候选书的章节标题对得上才归属，并且只复制、不搬运。"""

    legacy_output = library.output_root(web_service)
    (legacy_output / "html").mkdir(parents=True, exist_ok=True)
    (legacy_output / "html" / "第0001章.html").write_text("<html>旧成品</html>", encoding="utf-8")
    (legacy_output / "index_entries.json").write_text(
        json.dumps([[1, "第一章 起程"]], ensure_ascii=False), encoding="utf-8"
    )
    body = ("第一章 起程\n" + "正" * 400 + "\n第二章 深夜\n" + "文" * 400).encode()
    digest = hashlib.sha256(body).hexdigest()
    upload = library.input_root(web_service) / "uploads" / digest
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "source.txt").write_bytes(body)

    summary = library.migrate_legacy(web_service)

    book_id = digest[:12]
    assert summary["books"] == [{"id": book_id, "name": "source", "chapters": 2}]
    assert summary["attributed_to"]["id"] == book_id
    assert summary["attributed_to"]["matched_chapters"] == 1
    moved = library.book_paths(web_service, book_id, ".txt").output
    assert (moved / "html" / "第0001章.html").is_file()
    assert (moved / "index_entries.json").is_file()
    log = json.loads((legacy_output / "migration_log.json").read_text(encoding="utf-8"))
    assert log["attributed_to"]["id"] == book_id
    # 只增不删：旧目录里的原件仍在，运行状态等混用的文件故意不搬
    assert (legacy_output / "html" / "第0001章.html").is_file()


def test_migration_leaves_unmatched_output_in_place(web_service):
    legacy_output = library.output_root(web_service)
    (legacy_output / "html").mkdir(parents=True, exist_ok=True)
    (legacy_output / "html" / "第0009章.html").write_text("<html>别人的</html>", encoding="utf-8")
    (legacy_output / "index_entries.json").write_text(
        json.dumps([[9, "第九章 无关的标题"]], ensure_ascii=False), encoding="utf-8"
    )
    body = ("第一章 起程\n" + "正" * 400).encode()
    digest = hashlib.sha256(body).hexdigest()
    upload = library.input_root(web_service) / "uploads" / digest
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "source.txt").write_bytes(body)

    summary = library.migrate_legacy(web_service)

    assert "attributed_to" not in summary
    assert summary["notes"]
    assert (legacy_output / "html" / "第0009章.html").is_file()
    assert not (library.books_root(web_service) / digest[:12] / "html").exists()


def test_progress_counts_report_the_way_chapters_failed(web_service):
    """「失败 7 章」要配上原因才可解释：billing 与 invalid_response 要分开统计。"""

    database = web_service.config.output_dir / "books" / "x" / "state.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE chapter_progress (
            chapter_id INTEGER PRIMARY KEY, status TEXT NOT NULL,
            inserted_count INTEGER NOT NULL DEFAULT 0, error_type TEXT, updated_at TEXT NOT NULL
        )"""
    )
    rows = [
        (1, "completed", 150, None),
        (2, "completed", 171, None),
        (3, "failed", 0, "billing"),
        (4, "failed", 0, "billing"),
        (5, "failed", 0, "invalid_response"),
        (6, "running", 0, None),
        (7, "failed", 0, None),
        (8, "pending", 0, None),
    ]
    connection.executemany(
        "INSERT INTO chapter_progress VALUES (?,?,?,?,'2026-09-22T00:00:00+00:00')", rows
    )
    connection.commit()
    connection.close()

    counts = library.progress_counts(database)

    # 组件的每个状态都要报出来：只报 running 会把崩溃恢复后的 pending 变成隐形章（8 行只看到 7）
    assert (counts["completed"], counts["failed"], counts["running"]) == (2, 4, 1)
    assert counts["pending"] == 1
    assert counts["reasons"] == {"billing": 2, "invalid_response": 1, "unknown": 1}
    # 页面要能指出是哪几章：按数量倒序，同一原因的章节号升序
    assert counts["failures"] == [
        {"reason": "billing", "count": 2, "chapters": [3, 4]},
        {"reason": "invalid_response", "count": 1, "chapters": [5]},
        {"reason": "unknown", "count": 1, "chapters": [7]},
    ]

    assert library.progress_counts(database.parent / "missing.sqlite3") == {
        "completed": 0,
        "failed": 0,
        "running": 0,
        "reasons": {},
        "failures": [],
    }
    database.write_bytes(b"not a database")
    assert library.progress_counts(database)["failed"] == 0


def test_migration_is_a_no_op_once_the_library_has_books(web_service):
    _register(web_service, "第一章 甲\n正文".encode(), "甲.txt")
    legacy_output = library.output_root(web_service)
    (legacy_output / "html").mkdir(parents=True, exist_ok=True)
    (legacy_output / "html" / "第0001章.html").write_text("<html>旧</html>", encoding="utf-8")

    assert library.migrate_legacy(web_service) is None
    assert not (legacy_output / "migration_log.json").exists()
    assert len(library.read_index(web_service)["books"]) == 1


def test_migration_skips_a_source_it_cannot_parse(web_service, monkeypatch):
    """坏掉的源文件只记一笔，不能让迁移（以及整页）停在它上面。"""

    legacy_output = library.output_root(web_service)
    (legacy_output / "html").mkdir(parents=True, exist_ok=True)
    body = "第一章 起程\n正文".encode()
    digest = hashlib.sha256(body).hexdigest()
    upload = library.input_root(web_service) / "uploads" / digest
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "source.txt").write_bytes(body)

    def broken(path):
        raise RuntimeError("这个文件读不动")

    monkeypatch.setattr("ielts_novel.processors.chapter_parser.parse_novel", broken)

    summary = library.migrate_legacy(web_service)

    assert summary["books"] == []
    assert any("解析失败" in note for note in summary["notes"])
    assert library.read_index(web_service)["books"] == {}

