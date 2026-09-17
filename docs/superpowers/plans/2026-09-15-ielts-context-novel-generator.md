# IELTS Context Novel Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable Python CLI that parses long Chinese novels, calls DeepSeek one chapter at a time, validates preservation and vocabulary density, and exports DOCX, HTML, and XLSX artifacts.

**Architecture:** Pydantic models connect isolated parser, vocabulary, provider, processor, storage, and exporter modules. SQLite provides atomic progress and glossary state; JSON/XLSX are snapshots, while filesystem outputs are written atomically per chapter.

**Tech Stack:** Python 3.13, openai, pydantic, PyYAML, python-dotenv, python-docx, ebooklib, beautifulsoup4, openpyxl, tenacity-compatible custom retry logic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-ielts-context-novel-generator-design.md`

## Global Constraints

- Never modify files under `input/` and never overwrite the source novel.
- Read `DEEPSEEK_API_KEY` from environment or ignored local `.env`; never log it or send it to HTML.
- Before batch confirmation, a non-dry-run invocation may process at most one chapter.
- Save every successful chapter immediately and skip completed chapters by default.
- Stop a batch after five consecutive chapter failures.
- Density hard range is 20–35 learning items per 500 Chinese characters, target 28.

---

### Task 1: Project foundation and typed configuration

**Files:** Create `pyproject.toml`, `.gitignore`, `.env.example`, `config.yaml`, `src/ielts_novel/config.py`, `src/ielts_novel/models.py`, `tests/test_config.py`.

**Interfaces:** Produces `AppConfig.load(path)`, `Chapter`, `Paragraph`, `ConvertedChapter`, `VocabularyItem`, and typed domain errors.

- [ ] Write tests proving defaults, YAML overrides, missing-key behavior, limit validation, and secret redaction.
- [ ] Run `python -m pytest tests/test_config.py -q` and confirm the tests fail before implementation.
- [ ] Implement the minimal typed configuration and models; keep secrets out of model repr and serialization.
- [ ] Run the focused test, then initialize Git and commit only non-secret files.

### Task 2: TXT/DOCX/EPUB chapter parser and detection report

**Files:** Create `src/ielts_novel/processors/chapter_parser.py`, `tests/test_chapter_parser.py`, `tests/fixtures/novel.*`.

**Interfaces:** Consumes `Chapter`; produces `ParseResult parse_novel(path)` and `write_detection_report(result, path)`.

- [ ] Write parameterized failing tests for `第1章`, `第一章`, `第001章`, `Chapter 1`, `卷一`, `番外`, `序章`, `终章`, encoding fallback, paragraph preservation, and low-confidence refusal.
- [ ] Run the focused parser tests and record the expected failures.
- [ ] Implement format readers, title classifier, stable IDs, source hashes, and report generation.
- [ ] Run parser tests and perform a read-only parse/dry-run against the copied novel.

### Task 3: Atomic progress and glossary storage

**Files:** Create `src/ielts_novel/storage/progress_store.py`, `src/ielts_novel/storage/glossary_store.py`, `tests/test_storage.py`.

**Interfaces:** Produces `ProgressStore.claim/complete/fail/list_*`, `GlossaryStore.select/update/export_json`, all transaction-safe.

- [ ] Write failing tests for interruption recovery, completed-chapter skipping, failed-only selection, stale claims, atomic JSON snapshots, occurrence stages, and review schedules.
- [ ] Run storage tests to establish red state.
- [ ] Implement SQLite schema, transactions, atomic replace, and lock retry.
- [ ] Run storage tests and inspect generated JSON fixtures.

### Task 4: Global vocabulary selector

**Files:** Create `src/ielts_novel/processors/vocabulary_selector.py`, `data/vocabulary_seed.json`, `tests/test_vocabulary_selector.py`.

**Interfaces:** Consumes chapter phase, density target, glossary state; produces deterministic `VocabularyPlan` with new/review lists.

- [ ] Write failing tests for phase ratios, CEFR distribution, type distribution, no low-value terms, repeat stages, and deterministic selection.
- [ ] Run focused tests and confirm failures.
- [ ] Implement weighted selection, eligibility windows, density budget, and seed-file validation.
- [ ] Run focused tests and validate that batch enablement rejects a seed outside 5000–7000 entries.

### Task 5: Provider abstraction and DeepSeek integration

**Files:** Create `src/ielts_novel/providers/base.py`, `src/ielts_novel/providers/deepseek_provider.py`, `src/ielts_novel/prompts.py`, `tests/test_deepseek_provider.py`.

**Interfaces:** Produces `ModelProvider.generate_chapter/retry_chapter/health_check/estimate_tokens` and `ProviderResult` containing content, model, usage, time, retry count, and safe error type.

- [ ] Write failing mocked-client tests for missing/invalid key, 429, 5xx, timeout, empty content, malformed JSON, `finish_reason=length`, jittered backoff, token usage, and secret-free logs.
- [ ] Run provider tests to establish red state.
- [ ] Implement OpenAI-compatible client creation, complete JSON prompts/examples, output typing, retry classification, adaptive max tokens, and redaction.
- [ ] Run provider tests and scan captured logs for the sentinel secret.

### Task 6: Converter and quality gate

**Files:** Create `src/ielts_novel/processors/chapter_converter.py`, `src/ielts_novel/processors/quality_checker.py`, `tests/test_quality_checker.py`, `tests/test_converter.py`.

**Interfaces:** Consumes `Chapter`, `VocabularyPlan`, `ModelProvider`; produces validated `ConvertedChapter` and `QualityReport`.

- [ ] Write failing tests for missing/extra/duplicate paragraphs, changed names/numbers, low/high density, missing meanings, excessive sentence insertions, content loss, repetition, Markdown/explanation, Flash retry and Pro escalation.
- [ ] Run focused tests to establish red state.
- [ ] Implement paragraph chunking/merge, hard and soft checks, retry escalation, raw-response persistence, and failure continuation.
- [ ] Run focused tests and prove a still-invalid Pro result is persisted only under `failed/`.

### Task 7: DOCX, HTML, XLSX and volume exporters

**Files:** Create `src/ielts_novel/exporters/docx_exporter.py`, `html_exporter.py`, `xlsx_exporter.py`, templates/assets, and `tests/test_exporters.py`.

**Interfaces:** Consumes converted chapters and glossary records; writes chapter/volume DOCX, chapter/index HTML, and glossary XLSX.

- [ ] Write failing structural tests for heading levels, bold vocabulary, pink shading, page breaks, glossary columns, HTML metadata/popover/toggle, volume grouping and TOC field.
- [ ] Run exporter tests to establish red state.
- [ ] Implement exporters with atomic output writes and escaped HTML.
- [ ] Run tests, render DOCX for visual inspection, open HTML locally, and inspect XLSX structure and rendered range.

### Task 8: CLI orchestration, limits, reports, and dry-run

**Files:** Create `main.py`, `src/ielts_novel/cli.py`, `src/ielts_novel/orchestrator.py`, `tests/test_cli.py`, `tests/test_orchestrator.py`.

**Interfaces:** Produces the requested CLI flags, `reports/usage.json`, 20-chapter checks, ETA logging, pause marker, and batch-confirmation gate.

- [ ] Write failing tests for every command form, mutually exclusive flags, dry-run no-client guarantee, chapter/token limits, completed skip, retry-failed, immediate chapter saves, pause/resume, and five-consecutive-failure stop.
- [ ] Run focused tests to establish red state.
- [ ] Implement selection, cost estimation, bounded concurrency, serialized commits, progress logs, periodic reports, and confirmation gate.
- [ ] Run focused tests and execute a zero-cost dry-run on the real novel.

### Task 9: End-to-end verification and one-chapter sample

**Files:** Create `tests/test_end_to_end.py`; generate runtime files under `output/` only.

**Interfaces:** Verifies all prior public interfaces together without changing source input.

- [ ] Add an end-to-end fake-provider test that interrupts, resumes, exports, and compares every source paragraph anchor.
- [ ] Run the complete test suite, static compilation, and secret scan.
- [ ] If a key is configured, run the redacted health check and a self-authored short-text API test; stop with actionable status if authentication or billing fails.
- [ ] Process only the first detected real chapter, render DOCX, inspect HTML/XLSX, and write density, CEFR, integrity, token, duration, and estimated-cost reports.
- [ ] Re-hash the source and copied input to prove neither changed, then report results without starting a batch.
