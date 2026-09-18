from __future__ import annotations

import pytest
from ielts_novel.cli import build_parser, choose_chapter_ids
from ielts_novel.config import AppConfig
from ielts_novel.orchestrator import RunLimitError, enforce_run_limits, estimate_dry_run


def test_cli_accepts_requested_command_forms():
    parser = build_parser()
    assert parser.parse_args(["--start", "1", "--end", "20"]).start == 1
    assert parser.parse_args(["--resume"]).resume
    assert parser.parse_args(["--retry-failed"]).retry_failed
    assert parser.parse_args(["--chapter", "15"]).chapter == 15
    assert parser.parse_args(["--start", "1", "--end", "20", "--dry-run"]).dry_run
    assert parser.parse_args(["--build-volumes"]).build_volumes
    assert parser.parse_args(["--build-vocabulary"]).build_vocabulary


def test_chapter_selection_and_completed_skip():
    args = build_parser().parse_args(["--start", "1", "--end", "4"])
    assert choose_chapter_ids(args, total=10, completed={2, 4}, failed=set()) == [1, 3]
    retry = build_parser().parse_args(["--retry-failed"])
    assert choose_chapter_ids(retry, total=10, completed={2}, failed={3, 7}) == [3, 7]


def test_resume_is_cut_to_the_per_run_limit():
    resume = build_parser().parse_args(["--resume"])
    assert choose_chapter_ids(resume, total=2000, completed={1, 2}, failed=set(), limit=20) == list(range(3, 23))
    assert choose_chapter_ids(resume, total=2000, completed=set(), failed=set(), limit=20)[:3] == [1, 2, 3]
    assert len(choose_chapter_ids(resume, total=2000, completed=set(), failed=set(), limit=20)) == 20


def test_unconfirmed_non_dry_run_cannot_process_more_than_one_chapter():
    config = AppConfig(batch_confirmed=False, max_chapters_per_run=20)
    with pytest.raises(RunLimitError, match="样章确认"):
        enforce_run_limits([1, 2], 1000, config, dry_run=False)
    enforce_run_limits([1, 2], 1000, config, dry_run=True)


def test_configured_chapter_and_token_limits_stop_run():
    config = AppConfig(batch_confirmed=True, max_chapters_per_run=2, max_estimated_tokens_per_run=100)
    with pytest.raises(RunLimitError, match="章节上限"):
        enforce_run_limits([1, 2, 3], 10, config, dry_run=False)
    with pytest.raises(RunLimitError, match="Token"):
        enforce_run_limits([1], 101, config, dry_run=False)


def test_dry_run_estimates_without_provider():
    report = estimate_dry_run([1000, 1500], concurrency=3)
    assert report["estimated_chapters"] == 2
    assert report["chinese_characters"] == 2500
    assert report["api_requests"] == 2
    assert report["estimated_input_tokens"] > 0
    assert report["estimated_output_tokens"] > 0

