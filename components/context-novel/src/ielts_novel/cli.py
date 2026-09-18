from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ielts_novel.batch import BatchRunner
from ielts_novel.config import AppConfig, ConfigurationError, redact_secrets
from ielts_novel.exporters.txt_exporter import export_chapters_txt, load_chapters
from ielts_novel.orchestrator import (
    build_volume,
    enforce_run_limits,
    estimate_dry_run,
    process_single_chapter,
)
from ielts_novel.processors.chapter_parser import (
    ChapterDetectionError,
    parse_novel,
    write_detection_report,
)
from ielts_novel.processors.vocabulary_builder import VocabularyBuilder
from ielts_novel.processors.vocabulary_selector import validate_catalog
from ielts_novel.providers.deepseek_provider import DeepSeekProvider
from ielts_novel.storage.progress_store import ProgressStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="雅思词汇情境阅读生成器")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--chapter", type=int)
    selection.add_argument("--resume", action="store_true")
    selection.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--build-vocabulary", action="store_true", help="生成全局雅思词库（默认目标 6000 条）")
    parser.add_argument("--build-volumes", action="store_true", help="为已完成的 50 章区间生成分卷 Word")
    parser.add_argument("--export-txt", action="store_true", help="把已完成章节合并成单个 TXT（供阅读软件导入）")
    parser.add_argument("--txt-start", type=int)
    parser.add_argument("--txt-end", type=int)
    parser.add_argument("--txt-glossary", action="store_true", help="在 TXT 每章末尾附加本章核心词汇")
    parser.add_argument("--vocabulary-target", type=int, default=6000)
    parser.add_argument("--config", default="config.yaml")
    return parser


def choose_chapter_ids(args, *, total: int, completed: set[int], failed: set[int], limit: int | None = None) -> list[int]:
    if args.chapter:
        candidates = [args.chapter]
    elif args.retry_failed:
        candidates = sorted(failed)
    else:
        start, end = args.start or 1, args.end or total
        candidates = list(range(max(1, start), min(total, end) + 1))
    selected = [chapter_id for chapter_id in candidates if chapter_id not in completed]
    # A long novel has thousands of chapters: resume/range runs are always cut to the configured
    # per-run limit instead of being refused by the safety check.
    return selected[:limit] if limit else selected


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = AppConfig.load(args.config, require_api_key=args.health_check or args.build_vocabulary or not args.dry_run)
        if args.health_check:
            ok = DeepSeekProvider(config).health_check()
            print(json.dumps({"status": "ok" if ok else "failed"}, ensure_ascii=False))
            return 0 if ok else 1
        if args.export_txt:
            store = ProgressStore(config.output_dir / "state.sqlite3", config.output_dir / "progress.json")
            completed = set(store.completed_ids())
            start = args.txt_start or (min(completed) if completed else 1)
            end = args.txt_end or (max(completed) if completed else 1)
            chapters = load_chapters(config.output_dir / "chapter_json", start=start, end=end)
            target = config.output_dir / f"雅思情境阅读_第{start}至{end}章.txt"
            export_chapters_txt(chapters, target, include_glossary=args.txt_glossary)
            print(json.dumps({"txt": target.name, "chapters": len(chapters), "bytes": target.stat().st_size}, ensure_ascii=False))
            return 0
        if args.build_volumes:
            store = ProgressStore(config.output_dir / "state.sqlite3", config.output_dir / "progress.json")
            completed = set(store.completed_ids())
            size = config.volume_size
            built = []
            for start in range(1, (max(completed) if completed else 0) + 1, size):
                end = start + size - 1
                if all(chapter_id in completed for chapter_id in range(start, end + 1)):
                    path = build_volume(config.output_dir, start=start, end=end, volume_index=(end + size - 1) // size)
                    if path:
                        built.append(path.name)
            print(json.dumps({"volumes": built}, ensure_ascii=False))
            return 0
        if args.build_vocabulary:
            seed = validate_catalog("data/vocabulary_seed.json") if Path("data/vocabulary_seed.json").exists() else []
            builder = VocabularyBuilder(DeepSeekProvider(config), config.vocabulary_path, target=args.vocabulary_target, concurrency=config.concurrency, seed=seed)
            report = builder.build()
            target = config.output_dir / "reports" / "vocabulary_build.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report.as_dict(), ensure_ascii=False))
            return 0
        inputs = sorted(path for path in config.input_dir.glob("*") if path.suffix.lower() in {".txt", ".docx", ".epub"})
        if not inputs:
            raise ConfigurationError(f"{config.input_dir} 中没有支持的小说文件")
        try:
            parsed = parse_novel(inputs[0])
        except ChapterDetectionError as exc:
            write_detection_report(exc.result, config.output_dir / "reports" / "chapter_detection.json")
            raise
        write_detection_report(parsed, config.output_dir / "reports" / "chapter_detection.json")
        store = ProgressStore(config.output_dir / "state.sqlite3", config.output_dir / "progress.json")
        ids = choose_chapter_ids(args, total=len(parsed.chapters), completed=set(store.completed_ids()), failed=set(store.failed_ids()), limit=config.max_chapters_per_run)
        counts = [sum(len(p.text) for p in parsed.chapters[index - 1].paragraphs) for index in ids]
        report = estimate_dry_run(counts, concurrency=config.concurrency)
        enforce_run_limits(ids, int(report["estimated_total_tokens"]), config, dry_run=args.dry_run)
        if args.dry_run:
            target = config.output_dir / "reports" / "dry_run.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            return 0
        catalog = validate_catalog(config.vocabulary_path, require_full=len(ids) > 1)
        provider = DeepSeekProvider(config)
        if len(ids) == 1:
            result = process_single_chapter(parsed.chapters[ids[0] - 1], catalog, provider, config)
            print(json.dumps({"chapter_id": ids[0], "quality": result.__dict__}, ensure_ascii=False))
            return 0
        runner = BatchRunner(config, parsed.chapters, catalog, provider)
        summary = runner.run_concurrent(ids) if config.concurrency > 1 else runner.run(ids)
        print(json.dumps(summary.as_dict(), ensure_ascii=False))
        return 0 if not summary.failed else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any failure is reported as exit code 2
        print(f"ERROR: {redact_secrets(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
