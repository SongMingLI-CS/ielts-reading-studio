"""情境小说的书库：源文件长期保留，成品按书隔离。

旧版本把所有书塞进同一个输出目录（``output/context-novel``）：导入新书只是替换
``active_source.json``，上一本的成品要么被同名文件覆盖、要么以“孤儿”形式留在原地，
页面上于是出现“顶部是这本、章节列表是那本”的错配。这个模块给每本书分配自己的
目录：

* 源文件：``input/context-novel/library/<id>/source.<ext>``，按 sha256 去重，重复上传
  命中同一份，原件永不删除；
* 成品：``output/context-novel/books/<id>/``（章节 html、docx、词汇表、进度库、报告、
  运行配置都在里面），生成只写当前书，换书不再互相覆盖；
* 索引：``input/context-novel/books.json`` 记录书名、来源文件名、字节数、导入时间、
  章节数与当前选中的书。

迁移是一次性的、只增不删：旧的 ``uploads/``、``active/`` 与 ``output/context-novel/``
原样留着，内容被复制到新布局里。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.pipeline.service import ReadingStudioService

INDEX_NAME = "books.json"
LIBRARY_NAME = "library"
BOOKS_NAME = "books"
SOURCE_STEM = "source"
TITLE_LIMIT = 80


@dataclass(frozen=True)
class BookPaths:
    """一本书在磁盘上的全部位置。"""

    book_id: str
    source: Path
    meta: Path
    output: Path
    report: Path
    run: Path
    config: Path
    log: Path


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def input_root(service: ReadingStudioService) -> Path:
    return service.config.input_dir / "context-novel"


def output_root(service: ReadingStudioService) -> Path:
    return service.config.output_dir / "context-novel"


def library_root(service: ReadingStudioService) -> Path:
    return input_root(service) / LIBRARY_NAME


def books_root(service: ReadingStudioService) -> Path:
    return output_root(service) / BOOKS_NAME


def index_path(service: ReadingStudioService) -> Path:
    return input_root(service) / INDEX_NAME


def book_paths(service: ReadingStudioService, book_id: str, extension: str = "") -> BookPaths:
    root = output_root(service) / BOOKS_NAME / book_id
    source = library_root(service) / book_id / f"{SOURCE_STEM}{extension}"
    return BookPaths(
        book_id=book_id,
        source=source,
        meta=library_root(service) / book_id / "book.json",
        output=root,
        report=root / "reports" / "chapter_detection.json",
        run=root / "run_status.json",
        config=root / "runtime-config.yaml",
        log=root / "reports" / "web-generation.log",
    )


def read_index(service: ReadingStudioService) -> dict[str, Any]:
    payload = read_json(index_path(service), {})
    if not isinstance(payload, dict):
        payload = {}
    books = payload.get("books")
    if not isinstance(books, dict):
        books = {}
    return {"active": payload.get("active"), "books": books}


def write_index(service: ReadingStudioService, payload: dict[str, Any]) -> None:
    write_json(index_path(service), payload)


def book_entry(service: ReadingStudioService, book_id: str) -> dict[str, Any] | None:
    return read_index(service)["books"].get(book_id)


def active_book_id(service: ReadingStudioService) -> str | None:
    return read_index(service)["active"]


def set_active_book(service: ReadingStudioService, book_id: str) -> None:
    payload = read_index(service)
    if book_id not in payload["books"]:
        raise KeyError(book_id)
    payload["active"] = book_id
    write_index(service, payload)


def generated_chapters(service: ReadingStudioService, book_id: str) -> int:
    """这本书已经产出 html 的章节数（以索引与文件同时存在为准）。"""

    book = book_paths(service, book_id, str(_extension_of(book_entry(service, book_id) or {})))
    entries = read_json(book.output / "index_entries.json", [])
    if not isinstance(entries, list):
        return 0
    return sum(
        1
        for item in entries
        if isinstance(item, list)
        and len(item) == 2
        and (book.output / "html" / f"第{int(item[0]):04d}章.html").is_file()
    )


def progress_counts(database: Path) -> dict[str, Any]:
    """按状态统计章节进度，并给出失败原因分布与涉及的章节号。

    组件的进度库一章一行（``failed`` 行会记 ``error_type``），所以「失败 7 章」这种数字只有
    配上原因和章节号才可解释、才能行动：7 章全是 ``billing``（余额不足）与 7 章分散在模型质量
    问题上，操作者要做的事完全不同。
    """

    counts: dict[str, Any] = {
        "completed": 0,
        "failed": 0,
        "running": 0,
        "reasons": {},
        "failures": [],
    }
    if not database.exists():
        return counts
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM chapter_progress GROUP BY status"
            ).fetchall()
            failures = connection.execute(
                "SELECT COALESCE(NULLIF(error_type, ''), 'unknown'), chapter_id "
                "FROM chapter_progress WHERE status='failed' ORDER BY chapter_id"
            ).fetchall()
    except sqlite3.Error:
        return counts
    counts.update({str(status): int(count) for status, count in rows})
    grouped: dict[str, list[int]] = {}
    for reason, chapter_id in failures:
        grouped.setdefault(str(reason), []).append(int(chapter_id))
    counts["reasons"] = {reason: len(chapters) for reason, chapters in grouped.items()}
    counts["failures"] = [
        {"reason": reason, "count": len(chapters), "chapters": chapters}
        for reason, chapters in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])
        )
    ]
    return counts


def _extension_of(entry: dict[str, Any]) -> str:
    recorded = str(entry.get("extension") or "")
    if recorded.startswith("."):
        return recorded
    name = str(entry.get("filename") or entry.get("name") or "")
    return Path(name).suffix.lower()


def source_title(path: Path) -> str | None:
    """epub / docx 里的 dc:title，取不到就返回 None，由调用方回退到文件名。"""

    suffix = path.suffix.lower()
    try:
        if suffix == ".epub":
            with zipfile.ZipFile(path) as archive:
                opf = next(
                    (name for name in archive.namelist() if name.lower().endswith(".opf")),
                    None,
                )
                if opf is None:
                    return None
                raw = archive.read(opf).decode("utf-8", "ignore")
        elif suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                raw = archive.read("word/core.xml").decode("utf-8", "ignore")
        else:
            return None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None
    match = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", raw, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()
    return title[:TITLE_LIMIT] or None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def register_book(
    service: ReadingStudioService,
    *,
    digest: str,
    filename: str,
    extension: str,
    payload_path: Path,
    chapters: int,
    confident: bool,
    bytes_written: int,
) -> dict[str, Any]:
    """把一份源文件登记进书库（按 sha256 去重），并把它设为当前生成目标。"""

    book_id = digest[:12]
    index = read_index(service)
    target = library_root(service) / book_id / f"{SOURCE_STEM}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(payload_path, target)
    entry = dict(index["books"].get(book_id) or {})
    entry.update(
        {
            "id": book_id,
            "sha256": digest,
            "filename": Path(filename).name,
            "extension": extension,
            "bytes": bytes_written,
            "imported_at": entry.get("imported_at") or _now(),
            "updated_at": _now(),
            "chapters": chapters,
            "confident": confident,
        }
    )
    if not entry.get("name"):
        # epub/docx 的 dc:title 比文件名可靠；旧布局里没记下原名的上传只剩 source.<ext>，
        # 这种情况会落到 "source"，需要时可直接改 books.json 里的 name。
        entry["name"] = source_title(target) or Path(filename).stem or book_id
    index["books"][book_id] = entry
    index["active"] = book_id
    write_index(service, index)
    write_json(book_paths(service, book_id, extension).meta, entry)
    return entry


def resolve_book(
    service: ReadingStudioService, requested: str | None = None
) -> dict[str, Any] | None:
    """选书规则：显式请求 → 当前书 → 最近导入的一本。"""

    index = read_index(service)
    books = index["books"]
    if requested and requested in books:
        return dict(books[requested], active=index.get("active") == requested)
    active = index.get("active")
    if active and active in books:
        return dict(books[active], active=True)
    if books:
        newest = max(books.values(), key=lambda item: str(item.get("imported_at") or ""))
        return dict(newest, active=False)
    return None


def list_books(service: ReadingStudioService) -> list[dict[str, Any]]:
    """书库列表：当前书排最前，其余按导入时间倒序，带每本书的成品统计。"""

    index = read_index(service)
    rows: list[dict[str, Any]] = []
    for book_id, entry in index["books"].items():
        extension = _extension_of(entry)
        paths = book_paths(service, book_id, extension)
        rows.append(
            {
                **entry,
                "id": book_id,
                "extension": extension,
                "active": index.get("active") == book_id,
                "generated": generated_chapters(service, book_id),
                "progress": progress_counts(paths.output / "state.sqlite3"),
                "has_output": (paths.output / "index_entries.json").is_file(),
                "source_exists": paths.source.is_file(),
            }
        )
    rows.sort(key=lambda item: str(item.get("imported_at") or ""), reverse=True)
    rows.sort(key=lambda item: not item["active"])
    return rows


#: 迁移时要搬进书目录的成品。运行状态、运行配置与 web-generation.log 混着多本书，
#: 故意不搬，留在旧目录里作为原始凭证。
LEGACY_ARTIFACTS: tuple[str, ...] = (
    "html",
    "chapters",
    "chapter_json",
    "volumes",
    "chunk_checkpoints",
    "raw_responses",
    "failed",
    "index_entries.json",
    "index.html",
    "glossary.json",
    "glossary.xlsx",
    "state.sqlite3",
)
LEGACY_USAGE = Path("reports") / "usage.json"


def _legacy_uploads(service: ReadingStudioService) -> list[tuple[str, Path]]:
    root = input_root(service) / "uploads"
    found: list[tuple[str, Path]] = []
    if not root.is_dir():
        return found
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or len(directory.name) != 64:
            continue
        source = next(
            (path for path in directory.glob(f"{SOURCE_STEM}.*") if path.is_file()), None
        )
        if source is not None:
            found.append((directory.name, source))
    return found


def migrate_legacy(service: ReadingStudioService) -> dict[str, Any] | None:
    """把旧布局（一个共享输出目录 + uploads/）搬进书库；已有书就什么都不做。

    归属规则：旧 ``index_entries.json`` 里的章节标题与哪本候选书的章节标题匹配得最多，
    那份成品就属于哪本书（候选书来自 ``uploads/`` 与 ``active_source.json``）。一个都
    匹配不上时不动它，并在迁移日志里写清楚，避免把别人的成品安到错的书上。
    """

    from ielts_novel.processors.chapter_parser import (
        ChapterDetectionError,
        parse_novel,
        write_detection_report,
    )

    if read_index(service)["books"]:
        return None

    legacy_output = output_root(service)
    uploads = _legacy_uploads(service)
    legacy_source = read_json(legacy_output / "active_source.json")
    legacy_active_input = next(
        (
            path
            for path in (input_root(service) / "active").glob(f"{SOURCE_STEM}.*")
            if path.is_file()
        ),
        None,
    )

    candidates: list[tuple[str, str, Path]] = [(digest, "", path) for digest, path in uploads]
    if legacy_source and legacy_source.get("sha256"):
        digest = str(legacy_source["sha256"])
        # uploads/<sha>/source.txt 只有 sha 目录名，名字取自旧记录的原始文件名
        recorded_name = str(legacy_source.get("name") or "")
        candidates = [
            (item_digest, recorded_name if item_digest == digest else name, path)
            for item_digest, name, path in candidates
        ]
        if all(digest != existing for existing, _, _ in candidates):
            source_path = legacy_active_input or Path(str(legacy_source.get("path") or ""))
            if source_path.is_file():
                candidates.append((digest, recorded_name, source_path))

    if not candidates and not (legacy_output / "index_entries.json").is_file():
        return None

    summary: dict[str, Any] = {
        "migrated_at": _now(),
        "books": [],
        "legacy_output_dir": str(legacy_output),
        "notes": [],
    }
    parsed: dict[str, list[str]] = {}
    for digest, recorded_name, source_path in candidates:
        try:
            result = parse_novel(source_path)
        except ChapterDetectionError as exc:  # 边界不可信也要登记，页面会提示
            result = exc.result
        except Exception as exc:  # noqa: BLE001 - 单本书解析不了不能让整页打不开
            summary["notes"].append(
                f"{source_path.name} 解析失败，已跳过：{exc.__class__.__name__}: {exc}"
            )
            continue
        entry = register_book(
            service,
            digest=digest,
            filename=recorded_name or source_path.name,
            extension=source_path.suffix.lower(),
            payload_path=source_path,
            chapters=len(result.chapters),
            confident=result.confident,
            bytes_written=source_path.stat().st_size,
        )
        paths = book_paths(service, entry["id"], entry["extension"])
        write_detection_report(result, paths.report)
        parsed[entry["id"]] = [chapter.chapter_title for chapter in result.chapters]
        summary["books"].append(
            {"id": entry["id"], "name": entry["name"], "chapters": entry["chapters"]}
        )

    wanted = _chapter_titles_from_index(legacy_output / "index_entries.json")
    if wanted:
        scores = {
            book_id: len(_normalized_titles(titles) & _normalized_titles(wanted))
            for book_id, titles in parsed.items()
        }
        best_id = max(scores, key=lambda key: scores[key]) if scores else None
        if best_id is None or scores[best_id] == 0:
            summary["notes"].append(
                f"旧成品（{len(wanted)} 章）与现有书都匹配不上，保留在 {legacy_output} 未归属"
            )
        else:
            target_root = book_paths(service, best_id, _extension_of(book_entry(service, best_id))).output
            copied: list[str] = []
            for name in LEGACY_ARTIFACTS:
                origin = legacy_output / name
                if not origin.exists():
                    continue
                destination = target_root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if origin.is_dir():
                    for item in origin.rglob("*"):
                        if item.is_file():
                            relative = item.relative_to(origin)
                            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(item, destination / relative)
                else:
                    shutil.copy2(origin, destination)
                copied.append(name)
            if (legacy_output / LEGACY_USAGE).is_file():
                destination = target_root / LEGACY_USAGE
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_output / LEGACY_USAGE, destination)
                copied.append(LEGACY_USAGE.as_posix())
            summary["attributed_to"] = {
                "id": best_id,
                "name": book_entry(service, best_id)["name"],
                "matched_chapters": scores[best_id],
                "copied": copied,
            }
            summary["notes"].append(
                f"旧成品 {scores[best_id]}/{len(wanted)} 章标题命中，归入《{book_entry(service, best_id)['name']}》"
            )

    if legacy_source and legacy_source.get("sha256"):
        digest = str(legacy_source["sha256"])[:12]
        if book_entry(service, digest):
            set_active_book(service, digest)

    write_json(legacy_output / "migration_log.json", summary)
    return summary

def _normalized_titles(titles: list[str]) -> set[str]:
    return {re.sub(r"\s+", "", title) for title in titles if title}


def _chapter_titles_from_index(path: Path) -> list[str]:
    entries = read_json(path, [])
    if not isinstance(entries, list):
        return []
    return [
        str(item[1])
        for item in entries
        if isinstance(item, list) and len(item) == 2
    ]
