from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from ielts_novel.config import AppConfig
from ielts_novel.exporters.docx_exporter import export_chapter_docx, export_volume_docx
from ielts_novel.exporters.html_exporter import export_chapter_html, export_index_html
from ielts_novel.exporters.xlsx_exporter import export_glossary_xlsx
from ielts_novel.models import Chapter, ConvertedChapter, InsertedTerm, UsageRecord, VocabularyItem
from ielts_novel.processors.chapter_converter import ChapterConverter, ChapterConversionError
from ielts_novel.processors.quality_checker import QualityChecker
from ielts_novel.processors.term_extractor import build_lookup, collect_annotations, extract_occurrences, merge_lookup, normalize_chapter, paragraph_lookup, repair_chapter_stacking, revert_last_occurrence, sentence_spans
from ielts_novel.processors.vocabulary_selector import VocabularySelector
from ielts_novel.providers.base import ModelProvider
from ielts_novel.storage.glossary_store import GlossaryStore
from ielts_novel.storage.progress_store import ProgressStore
from ielts_novel.storage.atomic import atomic_write_text

MINIMUM_DENSITY_PER_500 = 20
TARGET_DENSITY_PER_500 = 28
# Inner blocks only need visible progress: the strict 20-35 band is enforced on the assembled
# chapter, and `top_up_chapter` then repairs the paragraphs that under-delivered. Failing a block
# outright for a long, hard paragraph would waste the whole chapter's work.
BLOCK_MINIMUM_DENSITY_PER_500 = 7
C1_TARGET_SHARE = 0.22
COMMIT_LOCK = threading.Lock()


class RunLimitError(RuntimeError):
    pass


def split_chapter(chapter: Chapter, *, max_paragraphs: int = 8) -> list[Chapter]:
    return [chapter.model_copy(update={"paragraphs": chapter.paragraphs[index:index + max_paragraphs]}) for index in range(0, len(chapter.paragraphs), max_paragraphs)]


def block_candidates(plan, block_index: int, *, new_per_block: int = 8, review_per_block: int = 4, c1_share: float = 0.5):
    """Rotate the chapter vocabulary plan so each block gets a small, rotating candidate list.

    Large candidate dumps measurably suppress the model's willingness to replace words, so each
    block only receives a short window of the plan. The window keeps an explicit C1 slice
    (``c1_share``) because otherwise the model's own choices drift towards easier B1/B2 words.
    """

    def window(items: list[VocabularyItem], per_block: int, *, c1_first: bool = False) -> list[VocabularyItem]:
        if not items:
            return []
        size = min(per_block, len(items))
        if c1_first:
            hard = [item for item in items if item.cefr == "C1"]
            rest = [item for item in items if item.cefr != "C1"]
            ordered = hard + rest
        else:
            ordered = list(items)
        start = (block_index * size) % len(ordered)
        return [ordered[(start + offset) % len(ordered)] for offset in range(size)]

    new_items = list(plan.new)
    c1_count = min(len([item for item in new_items if item.cefr == "C1"]), max(1, round(new_per_block * c1_share)))
    c1_items = [item for item in new_items if item.cefr == "C1"]
    others = [item for item in new_items if item.cefr != "C1"]
    window_new = window(others, max(1, new_per_block - c1_count)) + window(c1_items, c1_count)
    return window_new, window(list(plan.review), review_per_block)


def estimate_dry_run(chapter_character_counts: list[int], *, concurrency: int) -> dict[str, int | float]:
    characters = sum(chapter_character_counts)
    input_tokens = sum(max(1, int(count / 1.5) + 1200) for count in chapter_character_counts)
    output_tokens = sum(max(2048, int(count * 1.8)) for count in chapter_character_counts)
    requests = len(chapter_character_counts)
    return {
        "estimated_chapters": requests,
        "chinese_characters": characters,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_total_tokens": input_tokens + output_tokens,
        "api_requests": requests,
        "estimated_processing_seconds": round((requests * 25) / max(concurrency, 1), 1),
    }


def enforce_run_limits(chapter_ids: list[int], estimated_tokens: int, config: AppConfig, *, dry_run: bool) -> None:
    if dry_run:
        return
    if not config.batch_confirmed and len(chapter_ids) > 1:
        raise RunLimitError("样章确认前每次最多处理一章")
    if len(chapter_ids) > config.max_chapters_per_run:
        raise RunLimitError("超过 MAX_CHAPTERS_PER_RUN 章节上限")
    if estimated_tokens > config.max_estimated_tokens_per_run:
        raise RunLimitError("超过 MAX_ESTIMATED_TOKENS_PER_RUN Token 上限")


def _write_usage(path: Path, records) -> None:
    current = {"input_tokens": 0, "output_tokens": 0, "requests": [], "updated_at": None}
    if path.exists():
        current.update(json.loads(path.read_text(encoding="utf-8")))
    for record in records:
        current["input_tokens"] += record.input_tokens
        current["output_tokens"] += record.output_tokens
        current["requests"].append(record.model_dump(mode="json"))
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2))


def _recover_direct(output: Path, prefix: str, source_chunk: Chapter, protected_terms: list[str] | None, *, checker: QualityChecker | None = None, catalog: list[VocabularyItem] | None = None, known_terms: dict[str, InsertedTerm] | None = None):
    checker = checker or QualityChecker()
    lookup = build_lookup(catalog or [])
    candidates = sorted((output / "raw_responses").glob(f"{prefix}_attempt_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        try:
            wrapper = json.loads(candidate.read_text(encoding="utf-8"))
            raw_chapter = ConvertedChapter.model_validate_json(wrapper["raw_content"])
            converted = repair_chapter_stacking(normalize_chapter(raw_chapter, lookup=lookup), lookup=lookup)
            report = checker.check(source_chunk, converted, protected_terms=protected_terms, lookup=known_terms)
            if not report.passed:
                continue
            usage = []
            if "input_tokens" in wrapper:
                usage.append(UsageRecord(chapter_id=source_chunk.chapter_id, model=wrapper.get("model", "unknown"), input_tokens=wrapper.get("input_tokens", 0), output_tokens=wrapper.get("output_tokens", 0), retry_count=wrapper.get("retry_count", 0), status="success"))
            return converted, usage
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return None


def _recover_chunk_from_raw(output: Path, prefix: str, source_chunk: Chapter, protected_terms: list[str] | None, *, checker: QualityChecker | None = None, catalog: list[VocabularyItem] | None = None, known_terms: dict[str, InsertedTerm] | None = None):
    """Rebuild a chunk from already recorded responses, free of charge.

    Recorded evidence is searched at the exact chunk prefix first and then, when the block was
    previously bisected, in the `_part_a`/`_part_b` halves that were already paid for.
    """
    direct = _recover_direct(output, prefix, source_chunk, protected_terms, checker=checker, catalog=catalog, known_terms=known_terms)
    if direct:
        return direct
    if len(source_chunk.paragraphs) <= 1:
        return None
    midpoint = len(source_chunk.paragraphs) // 2
    left_chunk = source_chunk.model_copy(update={"paragraphs": source_chunk.paragraphs[:midpoint]})
    right_chunk = source_chunk.model_copy(update={"paragraphs": source_chunk.paragraphs[midpoint:]})
    left = _recover_chunk_from_raw(output, f"{prefix}_part_a", left_chunk, protected_terms, checker=checker, catalog=catalog, known_terms=known_terms)
    right = _recover_chunk_from_raw(output, f"{prefix}_part_b", right_chunk, protected_terms, checker=checker, catalog=catalog, known_terms=known_terms)
    if not left or not right:
        return None
    combined = ConvertedChapter(
        chapter_id=source_chunk.chapter_id,
        chapter_title=source_chunk.chapter_title,
        paragraphs=[*left[0].paragraphs, *right[0].paragraphs],
    )
    report = (checker or QualityChecker()).check(source_chunk, combined, protected_terms=protected_terms, lookup=known_terms)
    if not report.passed:
        return None
    return combined, [*left[1], *right[1]]


def convert_chunk_with_fallback(
    chunk: Chapter,
    provider: ModelProvider,
    output: Path,
    target: list[VocabularyItem],
    review: list[VocabularyItem],
    *,
    protected_terms: list[str] | None = None,
    raw_name_prefix: str | None = None,
    catalog: list[VocabularyItem] | None = None,
    known_terms: dict[str, InsertedTerm] | None = None,
    initial_note: str | None = None,
):
    """Convert a chunk, bisecting at paragraph boundaries if density retries fail."""
    catalog_items = list(catalog if catalog is not None else [*target, *review])
    chunk_checker = QualityChecker(minimum_density=BLOCK_MINIMUM_DENSITY_PER_500, allow_discrete_rounding=True)
    if raw_name_prefix:
        recovered = _recover_chunk_from_raw(output, raw_name_prefix, chunk, protected_terms, checker=chunk_checker, catalog=catalog_items, known_terms=known_terms)
        if recovered:
            converted, usage = recovered
            report = chunk_checker.check(chunk, converted, protected_terms=protected_terms, lookup=known_terms)
            return converted, report, usage
    converter = ChapterConverter(
        provider,
        output,
        max_quality_attempts=3,
        checker=chunk_checker,
        raw_name_prefix=raw_name_prefix,
        initial_note=initial_note,
        catalog=catalog_items,
    )
    try:
        return converter.convert(chunk, target, review, protected_terms=protected_terms, known_terms=known_terms)
    except ChapterConversionError:
        if len(chunk.paragraphs) <= 1:
            raise
        midpoint = len(chunk.paragraphs) // 2
        left = chunk.model_copy(update={"paragraphs": chunk.paragraphs[:midpoint]})
        right = chunk.model_copy(update={"paragraphs": chunk.paragraphs[midpoint:]})
        left_result, _left_report, left_usage = convert_chunk_with_fallback(
            left,
            provider,
            output,
            target,
            review,
            protected_terms=protected_terms,
            raw_name_prefix=f"{raw_name_prefix}_part_a" if raw_name_prefix else None,
            catalog=catalog_items,
            known_terms=known_terms,
        )
        right_result, _right_report, right_usage = convert_chunk_with_fallback(
            right,
            provider,
            output,
            target,
            review,
            protected_terms=protected_terms,
            raw_name_prefix=f"{raw_name_prefix}_part_b" if raw_name_prefix else None,
            catalog=catalog_items,
            known_terms=known_terms,
        )
        combined = ConvertedChapter(
            chapter_id=chunk.chapter_id,
            chapter_title=chunk.chapter_title,
            paragraphs=[*left_result.paragraphs, *right_result.paragraphs],
        )
        report = chunk_checker.check(chunk, combined, protected_terms=protected_terms, lookup=known_terms)
        if not report.passed:
            raise ChapterConversionError(
                f"chapter {chunk.chapter_id} split result failed quality checks: {','.join(report.issues)}"
            )
        return combined, report, [*left_usage, *right_usage]


def trim_chapter_density(
    chapter: Chapter,
    converted: ConvertedChapter,
    *,
    maximum_per_500: float = 35,
    lookup: dict[str, InsertedTerm] | None = None,
) -> ConvertedChapter:
    """Deterministically restore the densest paragraphs' excess items back to Chinese.

    Over-insertion is a formatting problem, not a content problem, so it is repaired locally
    instead of paying for another model round (and instead of failing the whole chapter).
    """
    source_chars = {paragraph.id: len(re.findall(r"[\u3400-\u9fff]", paragraph.text)) for paragraph in chapter.paragraphs}
    cap = math.floor(sum(source_chars.values()) * maximum_per_500 / 500)
    current = converted
    for _ in range(200):
        counts: dict[str, int] = {}
        occurrences: dict[str, list] = {}
        total = 0
        for paragraph in current.paragraphs:
            found = extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, lookup))
            occurrences[paragraph.id] = found
            counts[paragraph.id] = len(found)
            total += len(found)
        if total <= cap:
            return current
        candidates = [
            paragraph
            for paragraph in current.paragraphs
            if counts[paragraph.id] > 1 and any(occurrence.term.meaning for occurrence in occurrences[paragraph.id])
        ]
        if not candidates:
            return current
        densest = max(candidates, key=lambda paragraph: (counts[paragraph.id] / max(1, source_chars.get(paragraph.id, 1)), counts[paragraph.id]))
        trimmed = revert_last_occurrence(densest, occurrences[densest.id], lookup)
        current = current.model_copy(
            update={"paragraphs": [trimmed if paragraph.id == densest.id else paragraph for paragraph in current.paragraphs]}
        )
    return current


def deterministic_top_up(
    chapter: Chapter,
    converted: ConvertedChapter,
    catalog: list[VocabularyItem],
    *,
    maximum_items: int | None = None,
    lookup: dict[str, InsertedTerm] | None = None,
    maximum_per_sentence: int = 3,
) -> ConvertedChapter:
    """Close a small density gap without another API call.

    The catalogue's Chinese meanings are used as anchors: when a paragraph still contains 凝视
    verbatim and the catalogue holds `gaze=凝视`, the word is replaced in place with
    `gaze（凝视）`. Nothing is added to the plot, the Chinese gloss stays, and the result is fully
    deterministic — which matters when a chapter is only one or two items short.
    """
    anchors = sorted(
        ((item.meaning.strip(), item) for item in catalog if item.meaning and 2 <= len(item.meaning.strip()) <= 8),
        key=lambda pair: -len(pair[0]),
    )
    source_by_id = {paragraph.id: paragraph.text for paragraph in chapter.paragraphs}
    source_chars = {pid: len(re.findall(r"[\u3400-\u9fff]", text)) for pid, text in source_by_id.items()}
    current = converted
    cap = maximum_items
    for _ in range(60):
        per_paragraph: dict[str, list] = {}
        total = 0
        for paragraph in current.paragraphs:
            found = extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, lookup))
            per_paragraph[paragraph.id] = found
            total += len(found)
        if cap is not None and total >= cap:
            return current
        progressed = False
        weakest = sorted(
            current.paragraphs,
            key=lambda paragraph: (len(per_paragraph[paragraph.id]) / max(1, source_chars.get(paragraph.id, 1)), len(per_paragraph[paragraph.id])),
        )
        for paragraph in weakest:
            updated = _insert_one_anchor(paragraph, anchors, per_paragraph[paragraph.id], lookup, maximum_per_sentence)
            if updated is None:
                continue
            current = current.model_copy(update={"paragraphs": [updated if item.id == paragraph.id else item for item in current.paragraphs]})
            progressed = True
            break
        if not progressed:
            return current
    return current


def _insert_one_anchor(paragraph, anchors, occurrences, lookup, maximum_per_sentence: int):
    text = paragraph.converted_text
    protected = [(match.start(), match.end()) for match in re.finditer(r"（[^）]*）", text)]
    existing = {occurrence.term.lemma.lower() for occurrence in occurrences}
    spans = sentence_spans(text)
    for meaning, item in anchors:
        if item.lemma.lower() in existing:
            continue
        for match in re.finditer(re.escape(meaning), text):
            if any(start <= match.start() < end for start, end in protected):
                continue
            span = next(((start, end) for start, end in spans if start <= match.start() < end), None)
            in_sentence = sum(1 for occurrence in occurrences if span and span[0] <= occurrence.start < span[1])
            if in_sentence >= maximum_per_sentence:
                continue  # never break the per-sentence cap to close a density gap
            replacement = f"{item.word}（{meaning}）"
            new_text = text[:match.start()] + replacement + text[match.end():]
            return paragraph.model_copy(
                update={
                    "converted_text": new_text,
                    "inserted_terms": [occurrence.term for occurrence in extract_occurrences(new_text, lookup=paragraph_lookup(paragraph, lookup))],
                }
            )
    return None


def uplift_c1_share(
    chapter: Chapter,
    converted: ConvertedChapter,
    catalog: list[VocabularyItem],
    *,
    target_share: float = 0.20,
    maximum_additions: int = 20,
    lookup: dict[str, InsertedTerm] | None = None,
) -> ConvertedChapter:
    """Raise the C1 share of a converted chapter using catalogue-anchored replacements.

    Every insertion replaces a Chinese word that the source paragraph already contains, so the
    context always matches and no new plot is invented; the Chinese gloss stays in parentheses.
    """
    source_by_id = {paragraph.id: paragraph.text for paragraph in chapter.paragraphs}
    c1_anchors = sorted(
        ((item.meaning.strip(), item) for item in catalog if item.cefr == "C1" and item.meaning and 2 <= len(item.meaning.strip()) <= 8),
        key=lambda pair: -len(pair[0]),
    )
    current = converted
    for _ in range(maximum_additions):
        per_paragraph: dict[str, list] = {}
        total = 0
        c1_count = 0
        for paragraph in current.paragraphs:
            found = extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, lookup))
            per_paragraph[paragraph.id] = found
            total += len(found)
            c1_count += sum(1 for occurrence in found if occurrence.term.cefr == "C1")
        if total and c1_count / total >= target_share:
            return current
        progressed = False
        # Prefer paragraphs whose Chinese text still contains a usable C1 anchor.
        ordered = sorted(
            current.paragraphs,
            key=lambda paragraph: sum(1 for meaning, _item in c1_anchors if meaning in source_by_id.get(paragraph.id, "")),
            reverse=True,
        )
        for paragraph in ordered:
            source = source_by_id.get(paragraph.id, "")
            if not any(meaning in source for meaning, _item in c1_anchors):
                continue
            updated = _insert_one_anchor(paragraph, c1_anchors, per_paragraph[paragraph.id], lookup, 3)
            if updated is None:
                continue
            current = current.model_copy(update={"paragraphs": [updated if item.id == paragraph.id else item for item in current.paragraphs]})
            progressed = True
            break
        if not progressed:
            return current
    return current


def c1_share(converted: ConvertedChapter, *, lookup: dict[str, InsertedTerm] | None = None) -> float:
    occurrences = [occurrence for paragraph in converted.paragraphs for occurrence in extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, lookup))]
    if not occurrences:
        return 0.0
    return sum(1 for occurrence in occurrences if occurrence.term.cefr == "C1") / len(occurrences)


def top_up_chapter(
    chapter: Chapter,
    converted: ConvertedChapter,
    provider: ModelProvider,
    output: Path,
    target: list[VocabularyItem],
    review: list[VocabularyItem],
    *,
    protected_terms: list[str] | None = None,
    catalog: list[VocabularyItem] | None = None,
    known_terms: dict[str, InsertedTerm] | None = None,
    rounds: int = 3,
    paragraphs_per_group: int = 6,
):
    """Close a chapter-level density gap by re-converting the weakest paragraphs only."""
    usage: list[UsageRecord] = []
    checker = QualityChecker()
    source_by_id = {paragraph.id: paragraph for paragraph in chapter.paragraphs}
    for round_index in range(1, rounds + 1):
        report = checker.check(chapter, converted, protected_terms=protected_terms, lookup=known_terms)
        if report.issues != ["density_too_low"]:
            return converted, report, usage
        required = math.ceil(int(report.metrics["chinese_chars"]) * MINIMUM_DENSITY_PER_500 / 500)
        deficit = required - int(report.metrics["inserted_count"])
        if deficit <= 0:
            return converted, report, usage
        def shortfall(paragraph):
            source_chars = max(1, len(re.findall(r"[\u3400-\u9fff]", source_by_id[paragraph.id].text)))
            expected = source_chars * TARGET_DENSITY_PER_500 / 500
            return len(paragraph.inserted_terms) - expected

        weakest = sorted(converted.paragraphs, key=lambda paragraph: (shortfall(paragraph), paragraph.id))[:paragraphs_per_group]
        group = [source_by_id[paragraph.id] for paragraph in weakest if paragraph.id in source_by_id]
        if not group:
            return converted, report, usage
        group_chapter = chapter.model_copy(update={"paragraphs": group})
        prefix = f"chapter_{chapter.chapter_id:04d}_topup{round_index}"
        try:
            replacement, _group_report, group_usage = convert_chunk_with_fallback(
                group_chapter,
                provider,
                output,
                target,
                review,
                protected_terms=protected_terms,
                raw_name_prefix=prefix,
                catalog=catalog,
                known_terms=known_terms,
                initial_note=f"整章仍缺少至少 {deficit} 项英文学习项，本块是补足段落：必须逐段达到 minimum_items，并优先补齐被指定的段落。",
            )
        except ChapterConversionError:
            return converted, report, usage
        usage.extend(group_usage)
        replaced = {paragraph.id: paragraph for paragraph in replacement.paragraphs}
        converted = converted.model_copy(
            update={"paragraphs": [replaced.get(paragraph.id, paragraph) for paragraph in converted.paragraphs]}
        )
    report = checker.check(chapter, converted, protected_terms=protected_terms, lookup=known_terms)
    return converted, report, usage


def commit_chapter(output: Path, glossary: GlossaryStore, chapter: Chapter, converted: ConvertedChapter, report, usage: list[UsageRecord]) -> dict[str, int]:
    """Persist glossary, usage and every export for one finished chapter.

    Callers serialise this through ``COMMIT_LOCK`` so concurrent chapters cannot corrupt the
    shared glossary, index page or spreadsheet.
    """
    for paragraph in converted.paragraphs:
        for term in paragraph.inserted_terms:
            glossary.record_occurrence(VocabularyItem(**term.model_dump()), chapter.chapter_id)
    glossary_items = glossary.all()
    cumulative = {item.lemma: item.occurrence_count for item in glossary_items}
    chapter_json = output / "chapter_json" / f"第{chapter.chapter_id:04d}章.json"
    atomic_write_text(chapter_json, json.dumps(converted.model_dump(mode="json"), ensure_ascii=False, indent=2))
    export_chapter_docx(converted, output / "chapters" / f"第{chapter.chapter_id:04d}章.docx", cumulative=cumulative)
    export_chapter_html(converted, output / "html" / f"第{chapter.chapter_id:04d}章.html", cumulative=cumulative)
    export_index_html(_update_index_entries(output, chapter.chapter_id, chapter.chapter_title), output / "index.html")
    glossary.export_json(output / "glossary.json")
    export_glossary_xlsx(glossary_items, output / "glossary.xlsx")
    _write_usage(output / "reports" / "usage.json", usage)
    return cumulative


def _update_index_entries(output: Path, chapter_id: int, title: str) -> list[tuple[int, str]]:
    """Append one chapter to the light-weight index manifest and return the whole ordered list."""
    path = output / "index_entries.json"
    entries: list[list] = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            entries = []
    by_id = {int(entry[0]): str(entry[1]) for entry in entries if isinstance(entry, list) and len(entry) == 2}
    by_id[chapter_id] = title
    ordered = sorted(by_id.items())
    atomic_write_text(path, json.dumps([[chapter_id, title] for chapter_id, title in ordered], ensure_ascii=False, indent=1))
    return ordered


def build_volume(output: Path, *, start: int, end: int, volume_index: int) -> Path | None:
    """Merge the chapters of one 50-chapter window into a single Word volume."""
    chapters: list[ConvertedChapter] = []
    for chapter_id in range(start, end + 1):
        path = output / "chapter_json" / f"第{chapter_id:04d}章.json"
        if not path.exists():
            return None
        chapters.append(ConvertedChapter.model_validate_json(path.read_text(encoding="utf-8")))
    if not chapters:
        return None
    target = output / "volumes" / f"第{volume_index:02d}卷_第{start}至{end}章.docx"
    return export_volume_docx(chapters, target)


def process_single_chapter(chapter: Chapter, catalog: list[VocabularyItem], provider: ModelProvider, config: AppConfig, *, protected_terms: list[str] | None = None):
    output = config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    progress = ProgressStore(output / "state.sqlite3", output / "progress.json")
    if not progress.claim(chapter.chapter_id):
        raise RunLimitError(f"第 {chapter.chapter_id} 章已完成，默认跳过")
    glossary = GlossaryStore(output / "state.sqlite3")
    try:
        chinese_chars = len(re.findall(r"[\u3400-\u9fff]", "".join(p.text for p in chapter.paragraphs)))
        desired = max(1, min(len(catalog), math.ceil(chinese_chars * config.density.target_per_500_chars / 500)))
        seen = {item.lemma for item in glossary.all()}
        plan = VocabularySelector(catalog).select(chapter.chapter_id, desired, seen_lemmas=seen)
        chunks = split_chapter(chapter, max_paragraphs=4)
        converted_parts = []
        usage = []
        known_terms: dict[str, InsertedTerm] = {}
        checkpoint_dir = output / "chunk_checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        for chunk_index, chunk in enumerate(chunks, 1):
            checkpoint = checkpoint_dir / f"chapter_{chapter.chapter_id:04d}_chunk_{chunk_index:02d}.json"
            if checkpoint.exists():
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                converted_part = ConvertedChapter.model_validate(saved["chapter"])
                chunk_usage = [UsageRecord.model_validate(item) for item in saved.get("usage", [])]
                cached_report = QualityChecker(minimum_density=BLOCK_MINIMUM_DENSITY_PER_500, allow_discrete_rounding=True).check(chunk, converted_part, protected_terms=protected_terms, lookup=known_terms)
                if cached_report.passed:
                    converted_parts.extend(converted_part.paragraphs)
                    usage.extend(chunk_usage)
                    known_terms.update(collect_annotations(converted_part))
                    continue
            prefix = None if len(chunks) == 1 else f"chapter_{chapter.chapter_id:04d}_chunk_{chunk_index:02d}"
            if prefix:
                recovered = _recover_chunk_from_raw(output, prefix, chunk, protected_terms, checker=QualityChecker(minimum_density=BLOCK_MINIMUM_DENSITY_PER_500, allow_discrete_rounding=True), catalog=catalog, known_terms=known_terms)
                if recovered:
                    converted_part, chunk_usage = recovered
                    converted_parts.extend(converted_part.paragraphs)
                    usage.extend(chunk_usage)
                    known_terms.update(collect_annotations(converted_part))
                    temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
                    temporary.write_text(json.dumps({"chapter": converted_part.model_dump(mode="json"), "usage": [item.model_dump(mode="json") for item in chunk_usage]}, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(checkpoint)
                    continue
            converted_part, _chunk_report, chunk_usage = convert_chunk_with_fallback(
                chunk,
                provider,
                output,
                block_candidates(plan, chunk_index - 1)[0],
                block_candidates(plan, chunk_index - 1)[1],
                protected_terms=protected_terms,
                raw_name_prefix=prefix,
                catalog=catalog,
                known_terms=known_terms,
            )
            converted_parts.extend(converted_part.paragraphs)
            usage.extend(chunk_usage)
            known_terms.update(collect_annotations(converted_part))
            temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
            temporary.write_text(json.dumps({"chapter": converted_part.model_dump(mode="json"), "usage": [item.model_dump(mode="json") for item in chunk_usage]}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(checkpoint)
        converted = ConvertedChapter(chapter_id=chapter.chapter_id, chapter_title=chapter.chapter_title, paragraphs=converted_parts)
        converted = trim_chapter_density(chapter, converted, lookup=known_terms)
        converted, report, top_up_usage = top_up_chapter(
            chapter,
            converted,
            provider,
            output,
            block_candidates(plan, 0)[0],
            block_candidates(plan, 0)[1],
            protected_terms=protected_terms,
            catalog=catalog,
            known_terms=known_terms,
        )
        usage.extend(top_up_usage)
        if not report.passed and report.issues == ["density_too_low"]:
            required = math.ceil(int(report.metrics["chinese_chars"]) * MINIMUM_DENSITY_PER_500 / 500)
            if required - int(report.metrics["inserted_count"]) <= 8:
                converted = deterministic_top_up(chapter, converted, catalog, maximum_items=required, lookup=known_terms)
                report = QualityChecker().check(chapter, converted, protected_terms=protected_terms, lookup=known_terms)
        if report.passed:
            # Raise the C1 share only after the chapter is otherwise valid, so a density repair can
            # never be undone by an uplift that overshoots the 35/500 ceiling.
            metadata = merge_lookup(known_terms, build_lookup(catalog))
            uplifted = uplift_c1_share(chapter, converted, catalog, target_share=C1_TARGET_SHARE, lookup=metadata)
            if uplifted != converted:
                uplifted_report = QualityChecker().check(chapter, uplifted, protected_terms=protected_terms, lookup=metadata)
                if uplifted_report.passed and c1_share(uplifted, lookup=metadata) >= c1_share(converted, lookup=metadata):
                    converted, report = uplifted, uplifted_report
        if not report.passed:
            raise RunLimitError(f"分块合并后的章节质量检查失败：{','.join(report.issues)}")
        with COMMIT_LOCK:
            commit_chapter(output, glossary, chapter, converted, report, usage)
        progress.complete(chapter.chapter_id, int(report.metrics["inserted_count"]))
        return report
    except Exception as exc:
        progress.fail(chapter.chapter_id, getattr(exc, "error_type", type(exc).__name__))
        raise
    finally:
        progress.close()
