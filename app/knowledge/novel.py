"""读取情景小说产出的术语库，整理成知识点条目。

数据来自 ``output/context-novel/``：

- ``glossary.json``：全书累计的术语（词/音标/词性/中文释义/CEFR/搭配/例句/首末章/复现次数）
- ``index_entries.json``：章节标题，用来把"第 3 章"写成"第 3 章 血尸"
- ``chapter_json/*.json``：每段的 ``plan`` 记录了"中文语境 → 英文表达"的替换对，
  这是小说侧独有的知识点：同一个表达是在什么中文语境里用的。

只读文件、不改动任何小说产物；解析结果按文件指纹缓存，页面刷新不会重复解析。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.config import AppConfig

from .normalize import kind_of, resolve_level, resolve_pos

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}
_MAX_CONTEXT_CHAPTERS = 60
_MAX_CONTEXTS_PER_TERM = 3


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _fingerprint(root: Path) -> str:
    glossary = root / "glossary.json"
    chapters = root / "chapter_json"
    try:
        stamp = f"{glossary.stat().st_mtime_ns}:{glossary.stat().st_size}"
    except OSError:
        stamp = "none"
    try:
        files = sorted(chapters.glob("*.json"))
        newest = max((item.stat().st_mtime_ns for item in files), default=0)
        stamp += f":{len(files)}:{newest}"
    except OSError:
        stamp += ":none"
    return stamp


def _chapter_contexts(root: Path) -> dict[str, list[dict[str, Any]]]:
    """中英语境对照：``lemma → [{chapter_id, zh, en}]``。"""
    chapters = sorted((root / "chapter_json").glob("*.json"))[-_MAX_CONTEXT_CHAPTERS:]
    contexts: dict[str, list[dict[str, Any]]] = {}
    for path in chapters:
        data = _read_json(path, {})
        try:
            chapter_id = int(data.get("chapter_id"))
        except (TypeError, ValueError):
            continue
        for paragraph in data.get("paragraphs", []):
            for pair in paragraph.get("plan", []):
                english = str(pair.get("en") or "").strip()
                chinese = str(pair.get("zh") or "").strip()
                if not english or not chinese:
                    continue
                bucket = contexts.setdefault(english.casefold(), [])
                if len(bucket) >= _MAX_CONTEXTS_PER_TERM:
                    continue
                if any(
                    item["chapter_id"] == chapter_id and item["en"] == english
                    for item in bucket
                ):
                    continue
                bucket.append({"chapter_id": chapter_id, "zh": chinese, "en": english})
    return contexts


def _chapter_titles(root: Path) -> dict[int, str]:
    titles: dict[int, str] = {}
    for item in _read_json(root / "index_entries.json", []):
        if isinstance(item, list) and len(item) == 2:
            try:
                titles[int(item[0])] = str(item[1])
            except (TypeError, ValueError):
                continue
    return titles


def _chapter_label(titles: dict[int, str], chapter_id: int | None) -> str:
    if chapter_id is None:
        return "情境小说"
    title = titles.get(chapter_id, "")
    return f"第 {chapter_id} 章{(' ' + title) if title else ''}"


def _term_row(
    entry: dict[str, Any],
    contexts: dict[str, list[dict[str, Any]]],
    titles: dict[int, str],
) -> dict[str, Any] | None:
    word = str(entry.get("word") or entry.get("lemma") or "").strip()
    if not word:
        return None
    key = str(entry.get("lemma") or word).strip().casefold()
    level = resolve_level(word, entry.get("cefr"))
    pos = resolve_pos(word, entry.get("part_of_speech"))
    first = entry.get("first_chapter")
    first = int(first) if isinstance(first, int) else None
    collocation = str(entry.get("collocation") or "").strip()
    # 中英语境：优先按词条本身匹配，其次按它的搭配匹配（模型给的替换对有时是整段搭配）
    own_contexts: list[dict[str, Any]] = []
    for candidate in (key, word.casefold(), collocation.casefold()):
        for context in contexts.get(candidate, []):
            if context not in own_contexts:
                own_contexts.append(context)
    return {
        "key": key,
        "headword": word,
        "kind": kind_of(word, entry.get("part_of_speech")),
        "phonetic": str(entry.get("phonetic") or "").strip(),
        "meaning": str(entry.get("meaning") or "").strip(),
        "example": str(entry.get("example_sentence") or "").strip(),
        "collocations": [collocation] if collocation else [],
        "occurrences": int(entry.get("occurrence_count") or 0),
        "category": str(entry.get("category") or "").strip(),
        "contexts": own_contexts[:_MAX_CONTEXTS_PER_TERM],
        "sources": [
            {
                "kind": "novel",
                "label": "情境小说 · " + _chapter_label(titles, first),
                "href": f"/novel/preview/{first}" if first else "/novel",
            }
        ],
        **level,
        **pos,
    }


def load_novel_terms(config: AppConfig) -> dict[str, Any]:
    """小说侧的全部知识点条目（含可用性与统计）。"""
    root = config.output_dir / "context-novel"
    empty = {
        "available": False,
        "terms": [],
        "chapter_titles": {},
        "chapter_count": 0,
        "raw_count": 0,
    }
    if not (root / "glossary.json").is_file():
        return empty
    cache_key = f"{root}:{_fingerprint(root)}"
    with _LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    entries = _read_json(root / "glossary.json", [])
    if not isinstance(entries, list):
        return empty
    titles = _chapter_titles(root)
    contexts = _chapter_contexts(root)
    terms = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = _term_row(entry, contexts, titles)
        if row is not None:
            terms.append(row)
    result = {
        "available": True,
        "terms": terms,
        "chapter_titles": titles,
        "chapter_count": len(titles),
        "raw_count": len(entries),
    }
    with _LOCK:
        if len(_CACHE) > 8:
            _CACHE.clear()
        _CACHE[cache_key] = result
    return result
