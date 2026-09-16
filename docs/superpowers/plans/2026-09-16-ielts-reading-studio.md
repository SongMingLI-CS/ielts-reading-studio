# IELTS Reading Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-local IELTS Academic Reading system that indexes very large Chinese source files, uses two independent DeepSeek agents to produce and review grounded English passages and questions, and delivers CLI, HTML, DOCX, JSON, and browser-based practice workflows.

**Architecture:** A shared Python application core owns parsing, Pydantic domain models, SQLite state, DeepSeek access, two-agent orchestration, deterministic validation, and exports. Typer and FastAPI are thin adapters over that core. Every generation stage is cached by source/config/prompt hash and committed atomically so a 2,000-chapter job can pause, resume, and isolate failures.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, Uvicorn, Jinja2, Typer, SQLAlchemy 2, OpenAI Python SDK, python-docx, EbookLib, BeautifulSoup4, PyYAML, python-dotenv, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-ielts-reading-studio-design.md`

## Global Constraints

- Support IELTS Academic Reading only; do not claim official IELTS scoring or official-test equivalence.
- Run locally on Windows and bind the web server to `127.0.0.1` by default.
- Read TXT, DOCX, EPUB, and Markdown sources without modifying the source file.
- Import and estimate operations must not construct a DeepSeek client or spend API tokens.
- Read the API key only from `DEEPSEEK_API_KEY` or a Git-ignored local `.env`; redact secrets from errors and logs.
- Use separate prompts, configuration, and usage records for Agent A and Agent B.
- Freeze a passage before Agent B writes questions; Agent A never writes questions.
- Permit at most two author revision rounds and two examiner revision rounds per generation unit.
- Default to a 20-unit batch and two concurrent generation units; persist after every stage.
- Require an explicitly approved sample before batch generation is enabled for a corpus.
- Store canonical packages as JSON; render HTML and DOCX from the canonical package only.
- Do not generate one DOCX containing an entire 2,000-chapter corpus.
- Follow TDD for every task and commit after each independently testable deliverable.

## File Map

```text
app/
├── __init__.py                 package version
├── config.py                   YAML, environment, safety limits, redaction
├── models.py                   Pydantic domain contracts
├── agents/
│   ├── base.py                 model provider result and errors
│   ├── deepseek.py             OpenAI-compatible DeepSeek adapter
│   ├── author.py               Agent A prompt and response handling
│   ├── examiner.py             Agent B review and assessment handling
│   └── prompts.py              versioned prompts and JSON examples
├── parsers/
│   ├── base.py                 parser protocol and parse result
│   ├── chapter_detection.py    shared title detection and confidence
│   ├── txt.py                  streaming text parser and encoding fallback
│   ├── docx.py                 Word parser
│   ├── epub.py                 spine-order EPUB parser
│   ├── markdown.py             heading-aware Markdown parser
│   └── registry.py             extension-to-parser dispatch
├── planning/
│   ├── units.py                short merge and long split rules
│   └── estimate.py             request/token range estimates
├── storage/
│   ├── database.py             SQLAlchemy engine, schema, sessions
│   ├── repositories.py         corpus, unit, job, stage, usage persistence
│   ├── artifacts.py            atomic JSON and raw-response storage
│   └── cache.py                deterministic stage cache keys
├── validators/
│   ├── passage.py              grounding, shape, and difficulty checks
│   ├── questions.py            type-specific assessment checks
│   └── reports.py              issue and quality-report models
├── pipeline/
│   ├── state.py                allowed status transitions
│   ├── unit_runner.py          one unit's author/examiner/validation loop
│   ├── batch_runner.py         bounded concurrency and pause/resume
│   └── service.py              application-facing orchestration API
├── exporters/
│   ├── json_exporter.py        canonical package copy/export
│   ├── html_exporter.py        standalone practice and study HTML
│   ├── docx_exporter.py        single passage and bounded workbooks
│   └── service.py              format dispatch and batch grouping
├── web/
│   ├── app.py                  FastAPI factory
│   ├── dependencies.py         service/database dependencies
│   ├── routes_corpora.py       import, preview, approval, planning
│   ├── routes_jobs.py          start, pause, resume, retry, progress
│   ├── routes_practice.py      exercise view, local scoring, analysis
│   └── schemas.py              web request/response models
└── cli.py                      Typer commands over pipeline service
templates/                      Jinja pages and partials
static/                         CSS and small browser scripts
tests/                          unit, integration, web, and fixtures
```

---

### Task 1: Project Foundation, Configuration, and Domain Models

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config.yaml`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/models.py`
- Create: `tests/test_config.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: no application code.
- Produces: `AppConfig.load(path, require_api_key=False)`, `redact_secrets(value)`, and every Pydantic type named in design section 6.

- [ ] **Step 1: Write configuration tests**

```python
def test_loads_yaml_without_api_key_for_offline_work(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("output_dir: generated\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.output_dir == tmp_path / "generated"
    assert config.author_model == "deepseek-flash"

def test_requires_key_only_for_api_work(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        AppConfig.load(tmp_path / "missing.yaml", require_api_key=True)

def test_redacts_keys_and_bearer_tokens():
    assert "secret-value" not in redact_secrets("Bearer secret-value")
    assert "sk-1234567890abcdef" not in redact_secrets("sk-1234567890abcdef")
```

- [ ] **Step 2: Run configuration tests and verify failure**

Run: `python -m pytest tests/test_config.py -q`

Expected: collection fails because `app.config` does not exist.

- [ ] **Step 3: Create packaging and safe configuration**

Set `requires-python = ">=3.12,<3.14"`, register `ielts-reading = "app.cli:main"`, declare the stack from the header, and add a `dev` extra containing `pytest`, `pytest-asyncio`, `httpx`, and `ruff`. Ignore `.env`, `.venv/`, `output/`, `input/`, `*.db`, caches, and generated artifacts.

Implement these exact configuration fields:

```python
class AppConfig(BaseModel):
    base_dir: Path
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    database_path: Path = Path("output/state.db")
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    author_model: str = "deepseek-flash"
    examiner_model: str = "deepseek-v4-pro"
    concurrency: int = Field(2, ge=1, le=8)
    batch_size: int = Field(20, ge=1, le=100)
    author_revision_limit: int = Field(2, ge=0, le=5)
    examiner_revision_limit: int = Field(2, ge=0, le=5)
    min_source_chars: int = Field(800, ge=100)
    max_merged_chapters: int = Field(4, ge=1, le=20)
    max_merged_chars: int = Field(4000, ge=500)
    split_source_chars: int = Field(6000, ge=1000)
    split_min_chars: int = Field(2500, ge=500)
    split_max_chars: int = Field(4500, ge=1000)
    max_units_per_run: int = Field(20, ge=1)
    max_estimated_tokens_per_run: int = Field(500_000, ge=1)
    max_consecutive_failures: int = Field(5, ge=1)
```

Resolve relative paths against the YAML file's parent. Load `.env` from that same directory. Do not include secret values in Pydantic validation errors.

- [ ] **Step 4: Run configuration tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: all configuration tests pass.

- [ ] **Step 5: Write domain-model tests**

```python
def test_generation_unit_requires_three_distinct_question_types():
    with pytest.raises(ValidationError):
        GenerationUnit(
            id="unit-1", corpus_id="corpus-1", source_chapter_ids=["c1"],
            source_text_hash="abc", difficulty=Difficulty.STANDARD,
            question_types=[QuestionType.MATCHING_HEADINGS] * 3,
            config_snapshot={}, prompt_version="1", status=UnitStatus.INDEXED,
        )

def test_reading_package_question_numbers_are_contiguous(valid_package):
    changed = valid_package.model_copy(deep=True)
    changed.question_groups[0].questions[0].number = 8
    with pytest.raises(ValidationError, match="contiguous"):
        ReadingPackage.model_validate(changed.model_dump())
```

- [ ] **Step 6: Implement the domain models**

Use string enums for `Difficulty`, `QuestionType`, and `UnitStatus`. Implement `Corpus`, `SourceParagraph`, `SourceChapter`, `GenerationUnit`, `BriefItem`, `SourceBrief`, `PassageParagraph`, `VocabularyEntry`, `ReadingPassage`, `Question`, `QuestionGroup`, `UsageRecord`, `QualityIssue`, `QualityReport`, and `ReadingPackage` with the exact fields from the spec. Add model validators for three distinct question types, unique paragraph labels, and contiguous question numbering starting at 1.

- [ ] **Step 7: Run the foundation suite**

Run: `python -m pytest tests/test_config.py tests/test_models.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit foundation**

```powershell
git add pyproject.toml .gitignore .env.example config.yaml app tests/test_config.py tests/test_models.py
git commit -m "feat: establish configuration and domain contracts"
```

---

### Task 2: SQLite State, Atomic Artifacts, and Cache Keys

**Files:**
- Create: `app/storage/__init__.py`
- Create: `app/storage/database.py`
- Create: `app/storage/repositories.py`
- Create: `app/storage/artifacts.py`
- Create: `app/storage/cache.py`
- Create: `tests/storage/test_database.py`
- Create: `tests/storage/test_artifacts.py`
- Create: `tests/storage/test_cache.py`

**Interfaces:**
- Consumes: `Corpus`, `SourceChapter`, `GenerationUnit`, `UsageRecord`, `UnitStatus`.
- Produces: `Database.create_schema()`, `Repository`, `ArtifactStore`, and `stage_cache_key(stage: str, source_hash: str, difficulty: str, question_types: list[str], model: str, prompt_version: str, parameters: dict[str, object]) -> str`.

- [ ] **Step 1: Write persistence and transition tests**

```python
def test_repository_round_trips_unit_and_compare_and_sets_status(repository, unit):
    repository.add_unit(unit)
    assert repository.get_unit(unit.id) == unit
    assert repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING)
    assert not repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)

def test_recover_running_units(repository, unit):
    repository.add_unit(unit.model_copy(update={"status": UnitStatus.PASSAGE_REVIEWING}))
    recovered = repository.recover_interrupted_units()
    assert recovered == [unit.id]
    assert repository.get_unit(unit.id).status == UnitStatus.AUTHOR_REVISION_REQUIRED
```

- [ ] **Step 2: Run storage tests and verify failure**

Run: `python -m pytest tests/storage/test_database.py -q`

Expected: import failure for `app.storage.database`.

- [ ] **Step 3: Implement schema and repositories**

Create SQLAlchemy tables for corpora, source chapters, generation units, jobs, stage attempts, usage records, and corpus approvals. Store Pydantic payloads as JSON text while indexing IDs, ordinal, status, corpus ID, job ID, and timestamps in columns. Use transactions and compare-and-set status updates to prevent two workers from claiming the same unit.

- [ ] **Step 4: Implement and test atomic artifact writes**

```python
def test_write_json_is_atomic_and_utf8(tmp_path):
    store = ArtifactStore(tmp_path)
    path = store.write_json("packages/u1.json", {"title": "水资源"})
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "水资源"
    assert list(path.parent.glob("*.tmp")) == []
```

Write to a UUID-suffixed temporary file in the destination directory, flush and close it, then use `os.replace`. Raw responses must live under `raw_responses/<unit-id>/<stage>-attempt-<n>.json`.

- [ ] **Step 5: Implement deterministic cache keys**

```python
def test_cache_key_changes_only_when_semantic_inputs_change():
    first = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})
    same = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})
    changed = stage_cache_key("author", "source", "advanced", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})
    assert first == same
    assert first != changed
```

Canonicalize dictionaries with sorted JSON keys and hash UTF-8 bytes with SHA-256.

- [ ] **Step 6: Run storage suite**

Run: `python -m pytest tests/storage -q`

Expected: all tests pass.

- [ ] **Step 7: Commit persistence**

```powershell
git add app/storage tests/storage
git commit -m "feat: persist resumable corpus and generation state"
```

---

### Task 3: Four-Format Parsing and Confident Chapter Detection

**Files:**
- Create: `app/parsers/__init__.py`
- Create: `app/parsers/base.py`
- Create: `app/parsers/chapter_detection.py`
- Create: `app/parsers/txt.py`
- Create: `app/parsers/docx.py`
- Create: `app/parsers/epub.py`
- Create: `app/parsers/markdown.py`
- Create: `app/parsers/registry.py`
- Create: `tests/parsers/test_chapter_detection.py`
- Create: `tests/parsers/test_formats.py`
- Create: `tests/fixtures/sample.md`

**Interfaces:**
- Consumes: `Corpus`, `SourceChapter`, `SourceParagraph`.
- Produces: `parse_source(path: Path) -> ParseResult`, where `ParseResult` contains format, encoding, source hash, chapters, confidence, and diagnostics.

- [ ] **Step 1: Write title-detection tests**

```python
@pytest.mark.parametrize("title", [
    "第1章 初见", "第一章 初见", "第001章 初见", "Chapter 12 Arrival",
    "卷二 风起", "序章", "终章", "番外 海边",
])
def test_recognizes_supported_titles(title):
    assert detect_heading(title) is not None

def test_rejects_sentence_that_only_mentions_a_chapter():
    assert detect_heading("他在第一章中已经解释过原因。") is None
```

- [ ] **Step 2: Implement shared chapter detection**

Normalize whitespace without changing paragraph text. Return `DetectedHeading(kind, ordinal, title)` and calculate confidence from heading count, ordinal continuity, preamble ratio, empty-chapter count, and median chapter length. A parse is confident only if it contains at least two usable chapters or the document supplies explicit structural headings.

- [ ] **Step 3: Write format tests**

```python
def test_txt_falls_back_to_gb18030(tmp_path):
    path = tmp_path / "book.txt"
    path.write_bytes("第一章 开始\n正文。\n第二章 后续\n后文。".encode("gb18030"))
    result = parse_source(path)
    assert result.encoding == "gb18030"
    assert [c.chapter_title for c in result.chapters] == ["第一章 开始", "第二章 后续"]

def test_markdown_uses_headings_as_boundaries(tmp_path):
    path = tmp_path / "book.md"
    path.write_text("# 第一章\n甲。\n# 第二章\n乙。", encoding="utf-8")
    assert len(parse_source(path).chapters) == 2
```

Add DOCX fixtures in the test using `python-docx` and EPUB fixtures using EbookLib so binary files are not committed.

- [ ] **Step 4: Implement all parsers and registry**

TXT must iterate lines and maintain byte/character offsets without loading a second copy of the entire file. DOCX reads non-empty body paragraphs and heading styles. EPUB follows spine order and extracts headings and paragraphs with BeautifulSoup. Markdown recognizes level 1–3 headings. Registry rejects unsupported extensions with `UnsupportedFormatError`.

- [ ] **Step 5: Add low-confidence diagnostics test**

```python
def test_unstructured_large_text_returns_diagnostics_not_one_giant_chapter(tmp_path):
    path = tmp_path / "broken.txt"
    path.write_text("没有章节边界的正文。" * 1000, encoding="utf-8")
    result = parse_source(path)
    assert not result.confident
    assert result.chapters == []
    assert "no_reliable_boundaries" in result.diagnostics
```

- [ ] **Step 6: Run parser suite**

Run: `python -m pytest tests/parsers -q`

Expected: all tests pass.

- [ ] **Step 7: Commit parsing**

```powershell
git add app/parsers tests/parsers tests/fixtures
git commit -m "feat: parse and index structured reading sources"
```

---

### Task 4: Generation-Unit Planning, Corpus Manifest, and Offline Estimates

**Files:**
- Create: `app/planning/__init__.py`
- Create: `app/planning/units.py`
- Create: `app/planning/estimate.py`
- Create: `tests/planning/test_units.py`
- Create: `tests/planning/test_estimate.py`
- Create: `tests/integration/test_large_manifest.py`

**Interfaces:**
- Consumes: `list[SourceChapter]`, `AppConfig`, difficulty, and three question types.
- Produces: `plan_units(chapters: list[SourceChapter], config: AppConfig, difficulty: Difficulty, question_types: list[QuestionType]) -> list[GenerationUnit]`, `estimate_run(units: list[GenerationUnit], author_revisions: int, examiner_revisions: int) -> RunEstimate`, and `build_manifest(corpus: Corpus, chapters: list[SourceChapter], units: list[GenerationUnit]) -> CorpusManifest`.

- [ ] **Step 1: Write short-merge and long-split tests**

```python
def test_merges_short_chapters_without_crossing_four_chapters(config, chapters):
    units = plan_units(chapters[:5], config, Difficulty.STANDARD, DEFAULT_TYPES)
    assert len(units[0].source_chapter_ids) <= 4
    assert units[0].source_character_count <= 4000

def test_marks_irreducibly_short_unit(config):
    tiny = [chapter("c1", "甲" * 100)]
    unit = plan_units(tiny, config, Difficulty.STANDARD, DEFAULT_TYPES)[0]
    assert unit.limited_source is True

def test_splits_six_thousand_character_chapter_on_paragraph_boundaries(config):
    units = plan_units([chapter_with_paragraphs(["甲" * 1000] * 7)], config, Difficulty.STANDARD, DEFAULT_TYPES)
    assert len(units) == 2
    assert all(2500 <= u.source_character_count <= 4500 for u in units)
```

- [ ] **Step 2: Implement deterministic unit planning**

Preserve source order. Prefer natural paragraph boundaries. Give every unit a stable UUIDv5 derived from corpus hash plus source chapter IDs and split offsets. Save exact source spans and the configuration snapshot.

- [ ] **Step 3: Write estimate tests**

```python
def test_estimate_counts_two_agents_and_possible_repairs(units):
    estimate = estimate_run(units, author_revisions=2, examiner_revisions=2)
    assert estimate.minimum_requests == len(units) * 3
    assert estimate.maximum_requests == len(units) * 7
    assert estimate.minimum_tokens < estimate.maximum_tokens
```

Use explicit heuristics: Chinese input tokens range from `chars / 2.2` to `chars / 1.4`; each base unit has one author call, one passage-review call, and one assessment call; revision limits add their maximum calls. Keep estimates as ranges and never show currency without configured per-token prices.

- [ ] **Step 4: Prove 2,000-chapter offline behavior**

```python
def test_two_thousand_chapter_manifest_is_stable_and_offline(tmp_path, monkeypatch, service):
    source = tmp_path / "huge.txt"
    source.write_text("\n".join(f"第{i}章\n" + "正文。" * 200 for i in range(1, 2001)), encoding="utf-8")
    monkeypatch.setattr("app.agents.deepseek.OpenAI", lambda **kwargs: pytest.fail("API client constructed"))
    manifest = service.import_source(source)
    assert manifest.chapter_count == 2000
    assert manifest.unit_count > 0
```

- [ ] **Step 5: Run planning and large-manifest tests**

Run: `python -m pytest tests/planning tests/integration/test_large_manifest.py -q`

Expected: all tests pass without a network call.

- [ ] **Step 6: Commit planning**

```powershell
git add app/planning tests/planning tests/integration/test_large_manifest.py
git commit -m "feat: plan and estimate large resumable corpora"
```

---

### Task 5: DeepSeek JSON Provider with Safe Retry Semantics

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/base.py`
- Create: `app/agents/deepseek.py`
- Create: `tests/agents/test_deepseek.py`

**Interfaces:**
- Consumes: `AppConfig` and JSON-oriented system/user prompts.
- Produces: `DeepSeekProvider.complete_json(request: ModelRequest) -> ModelResult`.

- [ ] **Step 1: Write provider tests using a fake OpenAI client**

```python
def test_requests_json_mode_and_records_usage(fake_client, config):
    fake_client.reply('{"ok":true}', prompt_tokens=12, completion_tokens=5)
    result = DeepSeekProvider(config, client=fake_client).complete_json(
        ModelRequest(stage="author", model="deepseek-flash", system="Return JSON.", user="JSON input: {}", max_tokens=100)
    )
    assert result.payload == {"ok": True}
    assert result.input_tokens == 12
    assert fake_client.last_request["response_format"] == {"type": "json_object"}

def test_empty_json_response_is_retryable(fake_client, config):
    fake_client.reply("")
    with pytest.raises(EmptyResponseError):
        DeepSeekProvider(config, client=fake_client).complete_json(request())
```

- [ ] **Step 2: Implement provider contracts and error taxonomy**

Define `ModelRequest`, `ModelResult`, `ProviderError`, `ProviderAuthError`, `ProviderBillingError`, `ProviderRateLimitError`, `ProviderServerError`, `EmptyResponseError`, `TruncatedResponseError`, and `InvalidResponseError`. Reject `finish_reason="length"` before JSON parsing.

- [ ] **Step 3: Implement official OpenAI-compatible request shape**

Use `OpenAI(api_key=config.deepseek_api_key.get_secret_value(), base_url=config.deepseek_base_url)`, `client.chat.completions.create`, `response_format={"type": "json_object"}`, and prompts that explicitly contain the word JSON plus a concrete output example. Do not use deprecated `frequency_penalty` or `presence_penalty`. Keep model names configurable.

- [ ] **Step 4: Implement bounded transport retries**

Retry rate limits, timeouts, and 5xx errors with delays of approximately 1, 2, and 4 seconds plus up to 25% random jitter. Tests inject a zero-sleep callable. Never retry 401, 402, invalid local configuration, or Pydantic schema failures at the transport layer.

- [ ] **Step 5: Run provider tests**

Run: `python -m pytest tests/agents/test_deepseek.py -q`

Expected: all tests pass and no real HTTP request occurs.

- [ ] **Step 6: Commit provider**

```powershell
git add app/agents tests/agents/test_deepseek.py
git commit -m "feat: add resilient DeepSeek JSON provider"
```

---

### Task 6: Independent Author and Examiner Agents

**Files:**
- Create: `app/agents/prompts.py`
- Create: `app/agents/author.py`
- Create: `app/agents/examiner.py`
- Create: `tests/agents/test_author.py`
- Create: `tests/agents/test_examiner.py`
- Create: `tests/fixtures/agent_payloads.py`

**Interfaces:**
- Consumes: `DeepSeekProvider`, source text, configuration, and prior structured feedback.
- Produces: `AuthorAgent.create_brief`, `AuthorAgent.write_passage`, `AuthorAgent.revise_passage`, `ExaminerAgent.review_passage`, and `ExaminerAgent.build_assessment`.

- [ ] **Step 1: Write prompt-separation tests**

```python
def test_author_never_receives_question_answers(recording_provider, author_agent, unit):
    author_agent.write_passage(unit, brief_fixture(), source_text="原文")
    request = recording_provider.requests[-1]
    assert "question_groups" not in request.user
    assert "answer_key" not in request.user

def test_examiner_builds_questions_from_frozen_passage(recording_provider, examiner_agent, package_seed):
    examiner_agent.build_assessment(package_seed.unit, package_seed.passage)
    request = recording_provider.requests[-1]
    assert package_seed.passage.model_dump_json() in request.user
    assert "source_text" not in request.user
```

- [ ] **Step 2: Implement versioned prompts with complete JSON examples**

Set constants `AUTHOR_PROMPT_VERSION = "1"` and `EXAMINER_PROMPT_VERSION = "1"`. Author prompts must forbid fabricated names, institutions, dates, statistics, studies, and quotations; every Passage paragraph returns `source_item_ids`. Examiner review returns `{passed, issues, requested_changes}`. Assessment output returns exactly three typed question groups and all answer evidence fields.

- [ ] **Step 3: Implement Agent A**

Use separate calls for `create_brief` and `write_passage`. Revision input contains the previous Passage and examiner issues, not the examiner's eventual questions. Validate every provider payload with Pydantic before returning it.

- [ ] **Step 4: Implement Agent B**

`review_passage` receives source brief, source text, and Passage so it can detect fidelity problems. `build_assessment` receives only the accepted Passage, selected types, counts, and difficulty. `repair_assessment` receives only failed group IDs and validation issues, then merges replacements into unchanged groups.

- [ ] **Step 5: Add malformed-response tests**

```python
def test_examiner_rejects_missing_evidence(recording_provider, examiner_agent, package_seed):
    recording_provider.payload = assessment_payload(evidence_quote=None)
    with pytest.raises(AgentSchemaError):
        examiner_agent.build_assessment(package_seed.unit, package_seed.passage)
```

- [ ] **Step 6: Run agent tests**

Run: `python -m pytest tests/agents/test_author.py tests/agents/test_examiner.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit agents**

```powershell
git add app/agents tests/agents tests/fixtures/agent_payloads.py
git commit -m "feat: separate author and examiner agent responsibilities"
```

---

### Task 7: Passage and Question Quality Gates

**Files:**
- Create: `app/validators/__init__.py`
- Create: `app/validators/reports.py`
- Create: `app/validators/passage.py`
- Create: `app/validators/questions.py`
- Create: `tests/validators/test_passage.py`
- Create: `tests/validators/test_questions.py`

**Interfaces:**
- Consumes: source text, `SourceBrief`, `ReadingPassage`, and `QuestionGroup` values.
- Produces: `validate_passage(source_text: str, brief: SourceBrief, passage: ReadingPassage) -> QualityReport` and `validate_questions(passage: ReadingPassage, groups: list[QuestionGroup], expected_total: int) -> QualityReport` with stable issue codes and affected IDs.

- [ ] **Step 1: Write Passage validation tests**

```python
def test_rejects_unsupported_numbers_and_entities(valid_passage, brief):
    valid_passage.paragraphs[0].text += " A 2025 Oxford study surveyed 8,000 people."
    report = validate_passage("没有年份、机构或数据。", brief, valid_passage)
    assert "unsupported_specific_fact" in report.codes

def test_rejects_bad_shape(valid_passage, brief):
    valid_passage.paragraphs = valid_passage.paragraphs[:3]
    report = validate_passage("原文", brief, valid_passage)
    assert "paragraph_count_out_of_range" in report.codes
```

- [ ] **Step 2: Implement Passage checks**

Check 700–900 words, 6–9 continuous labels, source-item references, source-brief coverage, Markdown fences, and unsupported years, percentages, large numbers, quoted claims, person names, and organization names. Keep lexical difficulty as transparent metrics: median sentence length, proportion of words outside a bundled frequency list, type-token ratio, and connective count. Store metrics without converting them into an official band score.

- [ ] **Step 3: Write question-type tests**

```python
def test_completion_answer_must_be_verbatim_and_within_limit(valid_passage, completion_group):
    completion_group.word_limit = 2
    completion_group.questions[0].answer = "words absent from passage"
    report = validate_questions(valid_passage, [completion_group], expected_total=1)
    assert {"answer_not_in_passage", "answer_exceeds_word_limit"} <= set(report.codes)

def test_not_given_requires_nearest_context_and_missing_information_reason(valid_passage, tfng_group):
    question = tfng_group.questions[0]
    question.answer = "NOT GIVEN"
    question.evidence_quote = ""
    question.chinese_explanation = "文中没有说明。"
    report = validate_questions(valid_passage, [tfng_group], expected_total=1)
    assert "not_given_evidence_incomplete" in report.codes
```

- [ ] **Step 4: Implement shared and type-specific question checks**

Validate three groups, expected totals by difficulty, contiguous numbering, real evidence substrings in the named paragraph, exact-word answers, word limits, heading surplus and uniqueness, multiple-choice option counts, unique correct answers, and non-empty distractor explanations. Stable issue records must contain `code`, `message`, `stage`, and `affected_ids`.

- [ ] **Step 5: Run validator tests**

Run: `python -m pytest tests/validators -q`

Expected: all tests pass.

- [ ] **Step 6: Commit validators**

```powershell
git add app/validators tests/validators
git commit -m "feat: enforce grounded passages and answerable questions"
```

---

### Task 8: Resumable Two-Agent Unit and Batch Pipeline

**Files:**
- Create: `app/pipeline/__init__.py`
- Create: `app/pipeline/state.py`
- Create: `app/pipeline/unit_runner.py`
- Create: `app/pipeline/batch_runner.py`
- Create: `app/pipeline/service.py`
- Create: `tests/pipeline/test_state.py`
- Create: `tests/pipeline/test_unit_runner.py`
- Create: `tests/pipeline/test_batch_runner.py`
- Create: `tests/integration/test_resume.py`

**Interfaces:**
- Consumes: repositories, artifact store, agents, validators, generation units, and config limits.
- Produces: `UnitRunner.run(unit_id)`, `BatchRunner.run(job_id)`, and `ReadingStudioService` methods used by CLI and web.

- [ ] **Step 1: Write state-machine tests**

```python
def test_state_machine_rejects_skipping_examiner():
    assert can_transition(UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING)
    assert not can_transition(UnitStatus.AUTHOR_GENERATING, UnitStatus.COMPLETED)

def test_paused_unit_can_resume_at_saved_stage():
    assert resume_target(UnitStatus.PAUSED, saved_stage=UnitStatus.EXAMINER_GENERATING) == UnitStatus.EXAMINER_GENERATING
```

- [ ] **Step 2: Implement one-unit happy path**

The exact stage sequence is brief generation, passage generation, Passage validation, examiner review, question generation, question validation, package commit, and completed transition. Save the structured output, raw response metadata, quality report, usage record, and cache key after every model call.

- [ ] **Step 3: Write directed-repair tests**

```python
def test_passage_issue_returns_only_to_author(unit_runner, fakes):
    fakes.examiner.review_results = [review_failed("unsupported_specific_fact"), review_passed()]
    result = unit_runner.run("u1")
    assert fakes.author.revise_calls == 1
    assert fakes.examiner.repair_calls == 0
    assert result.status == UnitStatus.COMPLETED

def test_question_issue_repairs_only_failed_group(unit_runner, fakes):
    fakes.question_validator.fail_once(group_id="g2")
    unit_runner.run("u1")
    assert fakes.examiner.repaired_group_ids == [["g2"]]
    assert fakes.author.revise_calls == 0
```

- [ ] **Step 4: Implement revision limits and terminal failures**

After two author revisions or two examiner revisions, transition to `needs_review`. On auth or billing errors, mark the job blocked and stop claiming new units. On ordinary unit failure, save diagnostics and continue until `max_consecutive_failures` is reached.

- [ ] **Step 5: Implement bounded batch concurrency**

Use a `ThreadPoolExecutor(max_workers=config.concurrency)` because the model client is synchronous and network-bound. Claim units transactionally. A pause request prevents new claims and lets in-flight stages finish and save. Enforce `max_units_per_run` and `max_estimated_tokens_per_run` before claiming. After every 20 completed units, atomically refresh `reports/corpus-summary.json` with completion rate, failure rate, mean calls per unit, difficulty distribution, question-type distribution, Token totals, and revision counts.

- [ ] **Step 6: Test restart recovery and cache reuse**

```python
def test_restart_resumes_after_frozen_passage_without_recalling_author(app_factory):
    first = app_factory(crash_after="passage_reviewing")
    with pytest.raises(SimulatedCrash):
        first.runner.run("u1")
    second = app_factory(existing_database=first.database, existing_output=first.output)
    second.repository.recover_interrupted_units()
    second.runner.run("u1")
    assert second.author.write_calls == 0
    assert second.examiner.assessment_calls == 1
```

- [ ] **Step 7: Run pipeline suite**

Run: `python -m pytest tests/pipeline tests/integration/test_resume.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit pipeline**

```powershell
git add app/pipeline tests/pipeline tests/integration/test_resume.py
git commit -m "feat: orchestrate resumable two-agent generation"
```

---

### Task 9: Canonical JSON, Standalone HTML, and Bounded DOCX Export

**Files:**
- Create: `app/exporters/__init__.py`
- Create: `app/exporters/json_exporter.py`
- Create: `app/exporters/html_exporter.py`
- Create: `app/exporters/docx_exporter.py`
- Create: `app/exporters/service.py`
- Create: `tests/exporters/test_json_exporter.py`
- Create: `tests/exporters/test_html_exporter.py`
- Create: `tests/exporters/test_docx_exporter.py`

**Interfaces:**
- Consumes: validated `ReadingPackage` values only.
- Produces: `export_json`, `export_html`, `export_docx`, and `export_workbooks(packages, size)`.

- [ ] **Step 1: Write export-consistency tests**

```python
def test_json_round_trip_is_canonical(valid_package, tmp_path):
    path = export_json(valid_package, tmp_path / "package.json")
    assert ReadingPackage.model_validate_json(path.read_text(encoding="utf-8")) == valid_package

def test_html_hides_answers_until_submission(valid_package, tmp_path):
    html = export_html(valid_package, tmp_path / "practice.html").read_text(encoding="utf-8")
    assert 'data-answer="' not in html
    assert "application/json" in html
    assert "查看解析" in html
```

Keep scoring answers in a JSON script block that is revealed only after local submission; avoid answer attributes next to inputs. The standalone file is a study aid, not a secure examination environment.

- [ ] **Step 2: Implement HTML renderer**

Render article labels, all eight supported question types, timer, answer collection, score, evidence highlights, Chinese explanations, distractor explanations, and clickable vocabulary. Escape all model-supplied content before insertion. Include CSS and JavaScript inline so the file works offline.

- [ ] **Step 3: Implement DOCX renderer and workbook bounds**

Create headings, Passage paragraphs, question instructions, answer spaces, a page break before answer key, evidence and explanations, and vocabulary table. `export_workbooks` must reject sizes outside 20–50 and produce multiple files; a test with 51 packages and size 20 must create three files containing 20, 20, and 11 packages.

- [ ] **Step 4: Run exporter tests**

Run: `python -m pytest tests/exporters -q`

Expected: all tests pass.

- [ ] **Step 5: Commit exporters**

```powershell
git add app/exporters tests/exporters
git commit -m "feat: export consistent reading practice artifacts"
```

---

### Task 10: Complete Typer CLI and Safety Confirmations

**Files:**
- Create: `app/cli.py`
- Create: `tests/cli/test_cli.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `ReadingStudioService` and exporter service.
- Produces: the nine commands defined in design section 10.

- [ ] **Step 1: Write offline CLI tests**

```python
def test_import_and_estimate_do_not_require_key(runner, sample_txt, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    imported = runner.invoke(app, ["import", str(sample_txt)])
    assert imported.exit_code == 0
    corpus_id = extract_id(imported.stdout)
    estimated = runner.invoke(app, ["estimate", corpus_id, "--range", "1-20", "--level", "standard"])
    assert estimated.exit_code == 0
    assert "预计 API 请求" in estimated.stdout

def test_generate_all_requires_literal_confirmation(runner, approved_corpus):
    result = runner.invoke(app, ["generate", approved_corpus, "--all"], input="no\n")
    assert result.exit_code != 0
    assert "未开始生成" in result.stdout
```

- [ ] **Step 2: Implement CLI commands**

Implement `import`, `inspect`, `estimate`, `sample`, `approve-sample`, `generate`, `resume`, `retry`, `export`, and `serve`. Range syntax accepts `1-20` or comma-separated ordinals. `generate --all` prints unit count and token/request ranges and requires the literal Chinese confirmation `确认全部生成` unless `--yes` is supplied in a non-interactive script.

- [ ] **Step 3: Enforce sample approval**

`generate` must fail with exit code 2 and a clear message until `approve-sample <corpus-id> <unit-id>` records explicit approval. Approval is rejected if the named unit is not completed.

- [ ] **Step 4: Run CLI tests and smoke help**

Run: `python -m pytest tests/cli -q`

Run: `python -m app.cli --help`

Expected: tests pass and help lists all commands.

- [ ] **Step 5: Commit CLI**

```powershell
git add app/cli.py tests/cli pyproject.toml
git commit -m "feat: expose safe offline and generation CLI workflows"
```

---

### Task 11: FastAPI Corpus, Configuration, and Job-Monitoring Pages

**Files:**
- Create: `app/web/__init__.py`
- Create: `app/web/app.py`
- Create: `app/web/dependencies.py`
- Create: `app/web/schemas.py`
- Create: `app/web/routes_corpora.py`
- Create: `app/web/routes_jobs.py`
- Create: `templates/base.html`
- Create: `templates/corpora/index.html`
- Create: `templates/corpora/import.html`
- Create: `templates/corpora/preview.html`
- Create: `templates/jobs/configure.html`
- Create: `templates/jobs/detail.html`
- Create: `templates/partials/unit_rows.html`
- Create: `static/app.css`
- Create: `static/jobs.js`
- Create: `tests/web/test_corpora.py`
- Create: `tests/web/test_jobs.py`

**Interfaces:**
- Consumes: `ReadingStudioService` and repositories.
- Produces: `create_app(config=None, service=None) -> FastAPI` and local pages for import, preview, planning, approval, and monitoring.

- [ ] **Step 1: Write import-page tests**

```python
def test_upload_imports_without_api_key(client, sample_txt, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with sample_txt.open("rb") as stream:
        response = client.post("/corpora/import", files={"source": ("book.txt", stream, "text/plain")})
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert "章节解析预览" in preview.text
    assert "第1章" in preview.text
```

- [ ] **Step 2: Implement safe local upload and preview**

Copy uploads into `input/<corpus-id>/` using a generated filename and preserved extension; never trust a browser filename as a path. Limit accepted extensions to `.txt`, `.docx`, `.epub`, and `.md`. Preview uses paginated chapters and allows boundary overrides stored as manifest edits, never source-file edits.

- [ ] **Step 3: Implement configuration and sample approval pages**

Validate difficulty, exactly three distinct question types, batch size, concurrency, and selected range on the server. Display the same estimate returned by the CLI. Require sample completion before showing the batch-start control.

- [ ] **Step 4: Implement job monitor controls**

Expose POST endpoints for pause, resume, failed-only retry, and cancellation. The detail page fetches `/jobs/<id>/units?after=<revision>` every two seconds only while the job is active, updates rows, and stops polling at a terminal status.

- [ ] **Step 5: Run web-management tests**

Run: `python -m pytest tests/web/test_corpora.py tests/web/test_jobs.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit web management**

```powershell
git add app/web templates static tests/web/test_corpora.py tests/web/test_jobs.py
git commit -m "feat: manage corpora and generation jobs in local web app"
```

---

### Task 12: Browser Practice, Local Scoring, Analysis, and Export Center

**Files:**
- Create: `app/web/routes_practice.py`
- Create: `templates/practice/session.html`
- Create: `templates/practice/results.html`
- Create: `templates/practice/analysis.html`
- Create: `templates/exports/index.html`
- Create: `static/practice.js`
- Create: `static/vocabulary.js`
- Create: `tests/web/test_practice.py`
- Create: `tests/web/test_exports.py`

**Interfaces:**
- Consumes: completed `ReadingPackage` values and exporter service.
- Produces: practice session, submission, scoring, analysis, and download routes.

- [ ] **Step 1: Write answer-visibility and scoring tests**

```python
def test_practice_page_does_not_render_answer_key(client, completed_unit):
    response = client.get(f"/practice/{completed_unit.id}")
    assert response.status_code == 200
    assert completed_unit.package.question_groups[0].questions[0].answer not in response.text

def test_submission_scores_normalized_exact_answers(client, completed_unit):
    response = client.post(f"/practice/{completed_unit.id}/submit", json={"answers": {"1": "  WATER   SUPPLY "}})
    assert response.status_code == 200
    assert response.json()["correct"] == 1
```

- [ ] **Step 2: Implement scoring rules**

Normalize Unicode, outer whitespace, repeated internal whitespace, and case. Accept exact `answer` or any `acceptable_answers`. For multi-select multiple choice, compare unordered option sets. Do not use fuzzy matching because it can mark an invalid IELTS word-limit answer correct.

- [ ] **Step 3: Implement practice and analysis pages**

The session page renders the Passage and all supported controls, stores elapsed time in the browser, and sends answers to the server. Only the submission response exposes correctness. Analysis highlights the named evidence paragraph and escaped evidence quote, and shows Chinese explanation, distractor explanations, and vocabulary details.

- [ ] **Step 4: Implement export center tests and routes**

```python
def test_export_center_refuses_unvalidated_package(client, needs_review_unit):
    response = client.post("/exports", data={"unit_ids": [needs_review_unit.id], "format": "docx"})
    assert response.status_code == 409

def test_export_center_creates_bounded_workbooks(client, completed_units):
    response = client.post("/exports", data={"unit_ids": [u.id for u in completed_units], "format": "docx", "workbook_size": 20})
    assert response.status_code == 303
```

- [ ] **Step 5: Run practice and export tests**

Run: `python -m pytest tests/web/test_practice.py tests/web/test_exports.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit browser practice**

```powershell
git add app/web/routes_practice.py templates/practice templates/exports static/practice.js static/vocabulary.js tests/web
git commit -m "feat: add local IELTS practice and analysis experience"
```

---

### Task 13: End-to-End Hardening, Real Sample Gate, and Operator Documentation

**Files:**
- Create: `tests/integration/test_end_to_end.py`
- Create: `tests/integration/test_failure_matrix.py`
- Create: `tests/integration/test_secret_redaction.py`
- Create: `scripts/verify.ps1`
- Create: `README.md`
- Create: `docs/operator-guide.md`
- Modify: `.env.example`
- Modify: `config.yaml`

**Interfaces:**
- Consumes: the complete application.
- Produces: one-command verification, documented local setup, sample approval procedure, and evidence that no API call occurs in offline workflows.

- [ ] **Step 1: Add full fake-provider end-to-end test**

```python
def test_import_sample_approve_batch_export_round_trip(studio, sample_txt, fake_provider):
    corpus = studio.import_source(sample_txt)
    sample = studio.generate_sample(corpus.id, Difficulty.STANDARD, DEFAULT_TYPES)
    assert sample.status == UnitStatus.COMPLETED
    studio.approve_sample(corpus.id, sample.id)
    job = studio.create_job(corpus.id, ordinals=range(2, 5), difficulty=Difficulty.STANDARD, question_types=DEFAULT_TYPES)
    studio.run_job(job.id)
    assert all(unit.status == UnitStatus.COMPLETED for unit in studio.list_job_units(job.id))
    paths = studio.export_job(job.id, formats={"json", "html", "docx"})
    assert {path.suffix for path in paths} == {".json", ".html", ".docx"}
```

- [ ] **Step 2: Add failure matrix**

Parameterize 401, 402, 429, 500, timeout, empty content, truncated content, invalid JSON, invalid Passage schema, Passage review rejection, invalid question evidence, and exceeded revision limits. Assert the exact job/unit state, saved error code, retry count, and whether subsequent units continue.

- [ ] **Step 3: Add secret-redaction regression test**

Scan captured logs, exception strings, SQLite text columns, raw-response metadata, and generated output files for a sentinel API key. The only file permitted to contain the sentinel during the test is the temporary `.env` fixture.

- [ ] **Step 4: Write verification script**

`scripts/verify.ps1` must stop on errors and run:

```powershell
python -m pytest -q
python -m compileall -q app
python -m ruff check app tests
python -m app.cli --help | Out-Null
```

- [ ] **Step 5: Write setup and operator documentation**

README must include Python setup, editable installation, `.env` creation, import, offline estimate, web launch, sample generation, sample approval, 20-unit batch, pause/resume, failed-only retry, and export commands. Operator guide must explain 2,000-chapter parsing, estimate interpretation, state meanings, artifact locations, revision limits, backup, and safe recovery after interruption.

- [ ] **Step 6: Run complete offline verification**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`

Expected: all tests, compilation, lint, and CLI smoke checks pass without requiring `DEEPSEEK_API_KEY`.

- [ ] **Step 7: Perform one real sample only after the user configures the key**

Run:

```powershell
ielts-reading import <user-selected-source-path>
ielts-reading estimate <corpus-id> --range 1-1 --level standard
ielts-reading sample <corpus-id> --level standard
```

Expected: one package reaches `completed`, usage is recorded, and HTML/DOCX/JSON exports render. Stop after this sample and ask the user to review it; do not approve or start a batch on the user's behalf.

- [ ] **Step 8: Commit hardening and documentation**

```powershell
git add tests/integration scripts README.md docs/operator-guide.md .env.example config.yaml
git commit -m "test: verify safe end-to-end reading generation"
```

---

## Final Acceptance Run

- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1` from a clean checkout.
- [ ] Import the synthetic 2,000-chapter fixture and confirm no API client is constructed.
- [ ] Start `ielts-reading serve` and confirm it reports `http://127.0.0.1:8000`.
- [ ] Complete the single real sample workflow only after the user supplies the local key and source.
- [ ] Review `git status --short`; only intentional ignored runtime files may remain.
- [ ] Record the test count, sample artifact paths, model names returned by the API, actual Token usage, and any unit requiring manual review in the final handoff.

## Official API References Used by the Executor

- DeepSeek Chat Completions: `https://api-docs.deepseek.com/api/create-chat-completion/`
- DeepSeek JSON Output: `https://api-docs.deepseek.com/guides/json_mode/`
- DeepSeek models and current pricing: `https://api-docs.deepseek.com/quick_start/pricing/`
- IELTS Academic Reading format: `https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-reading`

Before the real sample, re-check current official DeepSeek model names and pricing. Configuration values may change without requiring code changes.
