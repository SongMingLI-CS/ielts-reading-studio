# IELTS Reading Studio — 实施进度说明

> 本文件记录截至 **2026-09-17** 的实施进度、验证证据、关键设计裁定与后续计划。
> 完整方案见 `docs/superpowers/plans/2026-09-16-ielts-reading-studio.md`，
> 设计规格见 `docs/superpowers/specs/2026-09-16-ielts-reading-studio-design.md`。

## 1. 状态速览

**总体进度：13 个任务的代码与离线验收全部完成。**
DeepSeek 适配器与双智能体已接入应用，但真实样篇门禁尚未执行；到目前为止
**没有任何真实 API 调用、没有任何 Token 消耗**。真实验收必须等待用户提供本地密钥和指定源文件。

| 任务 | 内容 | 状态 | 提交 |
|---|---|---|---|
| 1 | 项目骨架、配置、领域模型 | ✅ 已完成（含 1 轮修复） | `585bd08` `992258f` |
| 2 | SQLite 状态、原子写入、缓存键 | ✅ 已完成（含 1 轮修复） | `baa64d9` `97c8897` |
| 3 | 四种格式解析与章节边界识别 | ✅ 已完成 | `465934f` `4649158` |
| 4 | 生成单元规划、语料清单、离线估算 | ✅ 已完成 | `384d4d9` |
| 5 | DeepSeek JSON 适配器 | ✅ 已完成 | `d45a34f` |
| 6 | 作者 Agent A 与考官 Agent B | ✅ 已完成 | `c250c5e` |
| 7 | Passage / 题目程序化质量门禁 | ✅ 已完成 | `bde43d3` |
| 8 | 可恢复的双智能体单元与批量流水线 | ✅ 已完成 | `ee960ba` |
| 9 | JSON / HTML / 有界 DOCX 导出 | ✅ 已完成 | `1a0037f` |
| 10 | Typer CLI 与安全确认 | ✅ 已完成 | `6264be1` |
| 11 | FastAPI 语料库与任务监控页面 | ✅ 已完成 | `da81231` |
| 12 | 浏览器练习、评分、解析与导出中心 | ✅ 已完成 | `da81231` |
| 13 | 端到端加固、真实样篇门禁、运维文档 | ✅ 离线完成；真实样篇待用户输入 | `fcf90ee` |

当前实现位于本地 `main`，相对 `origin/main` 包含任务 5–13 的新增提交。

## 2. 当前验证结果

| 检查项 | 命令 | 结果 |
|---|---|---|
| 单元 + 集成测试（两个套件） | `python -m pytest -q` | **724 passed**（主项目 605 + 情境小说组件 119） |
| 静态检查（全仓） | `python -m ruff check app tests migrations components/context-novel/src components/context-novel/tests` | **All checks passed!** |
| 语法编译 | `python -m compileall -q app components/context-novel/src` | 通过 |
| CLI 烟雾检查 | `python -m app.cli --help` | 通过，列出 13 个命令（含 `migrate`/`snapshot`/`worker`） |
| 统一离线门禁 | `bash scripts/verify.sh`（Windows：`scripts\verify.ps1`） | 通过，无需 API Key |
| 离线强制 | `tests/conftest.py` 的守卫 + `tests/integration/test_offline_guard.py` | 非回环连接与 DNS 解析被拦截 |
| 部署脚本 | `tests/scripts/` | `deploy.sh` 语法/`--check` 只读/`--dry-run` 不变更；`server-setup.sh` dry-run 零写入、`--apply` 不打印密钥、幂等、`LC_ALL=C` 不崩溃 |
| 数据库迁移 | `tests/storage/test_migrations.py` | 全新建库、旧库标记、幂等、未知版本拒绝、模型漂移 |
| 持久任务队列 | `tests/pipeline/test_queue.py`、`tests/pipeline/test_worker.py` | 竞争认领、租约过期重认领、pause/resume/cancel、幂等键、真实假 Provider 批任务 |
| 安全（阶段五） | `tests/security/` | CSRF、会话与 Cookie、限速与可信代理、Provider 预算、安全头与缓存、上传/路径、脱敏与错误面，共 103 项 |
| 手工安全验收 | `/tmp/p5_manual_acceptance.py`（TestClient 等价脚本） | 12/12 PASS |
| 2,000 章导入（离线） | 见第 4.4 节 | 2,000 章 / 1,000 单元 / 0.07 秒 / 无网络访问 |
| 假 Provider 端到端 | `tests/integration/test_end_to_end.py` | 三种难度 + 样篇审批 + 批量 + 三格式导出 |
| 失败矩阵与密钥扫描 | `test_failure_matrix.py` / `test_secret_redaction.py` | 通过 |

仓库内不含任何真实密钥：`.env` 未提交，`.env.example` 只有变量名。密钥哨兵回归会扫描
异常、SQLite 和输出产物，唯一允许含哨兵值的文件是测试临时目录中的 `.env`。

## 3. 已完成工作明细

### 3.1 任务 1：项目骨架、配置与领域契约

**交付物**

- `pyproject.toml`：Python 3.12–3.13、依赖栈（Pydantic 2 / FastAPI / Uvicorn / Jinja2 /
  Typer / SQLAlchemy 2 / OpenAI SDK / python-docx / EbookLib / BeautifulSoup4 / PyYAML /
  python-dotenv）、`dev` extra（pytest、pytest-asyncio、httpx、ruff）、
  控制台入口 `ielts-reading = app.cli:main`。
- `app/config.py`：`AppConfig.load(path, require_api_key=False)`、`redact_secrets(value)`、
  `ConfigurationError`。相对路径以 YAML 所在目录为基准；密钥只从环境变量或同目录 `.env`
  读取，YAML 中的密钥被忽略；修订上限硬性不超过 2；校验失败信息经过脱敏。
- `app/models.py`：`Difficulty`、`QuestionType`（8 种题型）、`UnitStatus`（12 种状态），
  以及 `Corpus`、`SourceChapter`、`GenerationUnit`、`SourceBrief`、`ReadingPassage`、
  `Question`、`QuestionGroup`、`UsageRecord`、`QualityIssue`、`QualityReport`、`ReadingPackage`。
  校验器强制：三组互不相同的题型、段落标签唯一、题号从 1 连续编号。
- `config.yaml`、`.env.example`、`.gitignore`。

**验证证据**：`tests/test_config.py`（6 项：离线加载、按需要求密钥、脱敏、显式 base_dir、
YAML 不得提供密钥、修订上限）、`tests/test_models.py`。

### 3.2 任务 2：可恢复状态、原子产物与缓存键

**交付物**

- `app/storage/database.py`：`Database.create_schema()`，SQLite + WAL + 外键 + busy_timeout；
  表包括 `corpora`、`source_chapters`、`generation_units`、`jobs`、`stage_attempts`、
  `usage_records`、`corpus_approvals`。Pydantic 载荷以 JSON 文本保存，同时把
  ID、序号、状态、归属和时间戳建成可索引列。
- `app/storage/repositories.py`：`Repository` 提供单元 compare-and-set 状态迁移
  （单条带状态谓词的 `UPDATE`，保证两个 worker 不能同时认领同一单元）、
  中断恢复（`running` 类状态回退到可恢复状态）、阶段尝试的
  创建/更新/完成/失败记录，以及 `get_cached_stage` 缓存复用；
  另有 `add_units`、`add_source_chapters` 批量写入（任务 4 新增，用于千章级语料）。
- `app/storage/artifacts.py`：`ArtifactStore` 以「同目录 UUID 临时文件 + `fsync` +
  `os.replace`」原子写入 UTF-8 JSON，并拒绝越出根目录的路径；原始响应固定存放于
  `raw_responses/<unit-id>/<stage>-attempt-<n>.json`。
- `app/storage/cache.py`：`stage_cache_key(...)` 用排序键的规范 JSON 做 SHA-256，
  只有语义输入变化时才会变化。

**验证证据**：`tests/storage/test_database.py`、`test_artifacts.py`、`test_cache.py`、
`test_stages.py`（含并发认领、缓存冲突、失败隔离、原子写入不留 `.tmp`）。

### 3.3 任务 3：四种输入格式解析与可信的章节边界

**交付物**

- `app/parsers/chapter_detection.py`：`detect_heading()` 识别
  `第1章 / 第一章 / 第001章 / Chapter 12`、`卷二 / 第一卷`、`序章 / 终章 / 番外`，
  同时拒绝「他在第一章中已经解释过原因。」这类句子（行首锚定、长度上限 40 字、
  含句读即拒绝）。中文数字解析支持 `十二`、`二十一`、`一百零五`、`两千`。
- 可信度模型：结构、序号连续性、前言占比、空章节数、章节长度中位数加权求和，
  低于 `0.6` 或纯文本不足两个可用章节时，返回 `chapters == []` 并给出
  `no_reliable_boundaries` 诊断，**绝不把整个文件当成一次模型输入**；
  被否决的切分保留在 `candidate_chapters` 里供预览页人工修正。
- `app/parsers/txt.py`：BOM → UTF-8 → GB18030 编码探测（容忍探测样本切断多字节字符）；
  两趟流式逐行扫描（第一趟收集标题偏移，第二趟组装章节），不在内存中保留两份正文，
  并记录章节级字符偏移。
- `app/parsers/docx.py`（标题样式优先、标题模式兜底）、
  `app/parsers/epub.py`（按 spine 顺序、跳过 nav、按文档内标题切分）、
  `app/parsers/markdown.py`（一至三级标题）。
- `app/parsers/registry.py`：`parse_source(path)`，按扩展名分发，
  对不支持格式抛 `UnsupportedFormatError`，文件不存在抛 `SourceNotFoundError`。
- 章节 ID 为 `UUIDv5(sha256(源文件) + 序号)`，重复导入同一文件得到完全相同的章节 ID 与偏移。

**验证证据**：`tests/parsers/test_chapter_detection.py`、`test_formats.py` 共 35 项；
另加 6 章 Markdown 夹具 `tests/fixtures/sample.md`（DOCX/EPUB 夹具在测试内临时生成，
不入库二进制文件）。

### 3.4 任务 4：生成单元规划、语料清单与离线估算

**交付物**

- `app/planning/units.py`
  - 规划规则：默认最小来源 800 中文字符；短章节按顺序向后合并，一次最多 4 章或
    4,000 字，以先到者为准；合并到上限仍不足 800 字时仍可生成，但标记
    `limited_source=true`；单章超过 6,000 字时按自然段切成 2,500–4,500 字的单元，
    并在必要时把整段回拨给末段以避免尾段过短；单段本身超长时按句读/字符切分。
  - 每个单元记录 `ordinal`、`source_character_count`、精确 `source_spans`
    （章 ID + 字符区间）、`source_text_hash`、`config_snapshot`（不含路径与密钥）、
    状态 `indexed`；单元 ID 为 `UUIDv5(语料 ID + 章节 ID + 区间)`，重复规划完全一致。
  - `unit_source_text()` 可把单元精确还原为发送给模型的源文本。
  - 内置设计规格规定的默认题型组合与题量：Foundation 10 题、Standard 12 题、Advanced 13 题。
- `app/planning/estimate.py`：`estimate_run(units, author_revisions, examiner_revisions)`。
  按「每单元 4 次基础调用（brief、passage、passage review、assessment）」计算，
  作者返工每轮追加 2 次调用（重写 + 复审），考官返工每轮追加 1 次调用（局部重写题组）；
  中文输入 Token 区间采用 `字符数/2.2 ~ 字符数/1.4`。
  未配置单价时 `pricing_available=False`，**不显示任何虚构金额**。
- `app/planning/importer.py`：`CorpusImporter.import_source(path)` 完成
  「只读解析 → 规划单元 → 生成清单 → 落盘 `output/<slug>/manifest.json` → 写 SQLite」，
  全程不导入任何 Agent 模块、不建立 API 客户端。同一文件重复导入会直接复用既有清单。

**验证证据**：`tests/planning/test_units.py`（14 项）、`tests/planning/test_estimate.py`（6 项）、
`tests/integration/test_large_manifest.py`（3 项，其中包含 2,000 章离线导入，
用 `socket.socket` 拦截证明导入期间没有任何网络访问，并断言 `app.agents` 未被导入）。

**2,000 章实测**

```text
源文件：3.46 MB，2000 章，1,200,000 字
解析置信度：0.9625，诊断为空，解析耗时 0.05 秒
规划结果：2000 章 → 1000 个生成单元
导入总耗时：0.07 秒
manifest.json：1.73 MB（只存章节边界摘要 + 单元，不重复正文）
SQLite：2000 章、1000 单元全部落库
```

## 4. 已建立的工程约定

1. **TDD 优先**：每个任务先写测试并确认 RED（模块不存在/断言失败），再实现到 GREEN。
2. **小步提交**：每个可独立验证的交付物一次提交；纯格式化/静态检查修复单独提交
   （如 `4649158 style: make repository lint-clean under ruff`）。
3. **离线可验证**：任务 1–4 的任何操作都不会建立 DeepSeek 客户端、不产生费用；
   测试用注入的假客户端/假 socket 覆盖。
4. **密钥卫生**：`.env` 不入库；`redact_secrets()` 统一遮盖 `Bearer`、`sk-`、
   `DEEPSEEK_API_KEY=`；`config_snapshot` 明确排除路径与密钥；错误信息同样脱敏。
5. **静态检查**：`ruff` 全仓零告警，作为每个任务完成门禁的一部分。
6. **大任务可恢复**：单元状态、阶段尝试、缓存键全部落 SQLite；已被证明可
   并发认领互斥、可中断恢复、可复用已完成阶段。

## 5. 已完成的应用层能力（任务 5–13 摘要）

| 能力 | 实现结果 |
|---|---|
| DeepSeek 适配器 | JSON 模式、错误分类、1/2/4 秒加抖动的有界重试，截断先于 JSON 解析被拒绝 |
| 双智能体 | Agent A 只写文章，Agent B 只审稿与命题；提示词、模型、阶段和用量记录相互独立 |
| 质量门禁 | Passage 形状/来源/具体事实/透明难度指标，以及全部题型的编号、证据、答案和类型约束 |
| 流水线 | 阶段缓存、原子落盘、定向返工、2+2 上限、滚动有界并发、暂停恢复、失败隔离和汇总报告 |
| 导出 | 规范 JSON、离线单文件 HTML、单篇 DOCX 与 20–50 篇有界练习册 |
| CLI | `import / inspect / estimate / sample / approve-sample / generate / resume / retry / export / serve`，全量生成需字面确认 |
| 本地网页 | 安全上传、分页预览、独立边界修正、配置/估算/审批、任务监控、练习评分、解析和导出 |
| 验收 | 三难度假 Provider 端到端、失败矩阵、密钥扫描、`scripts/verify.ps1`、README 与运维指南 |

## 5.1 后续迭代（服务器上线后追加）

项目在公网服务器上跑起来后，又按实际使用反馈做了几轮迭代，均已部署到
`http://192.144.160.196:8766`：

| 能力 | 实现结果 |
|---|---|
| 提交可靠性 | 练习提交同时接受 JSON 与表单，纯 HTTP 环境下 `crypto.randomUUID()` 缺失时回退，交卷后 303 跳转服务端结果页 |
| 语料库资料库 | 进度环、状态徽章、分页、结构化边界编辑、重绑定与删除；身份 = 源文件 SHA-256 |
| 生成题目配置页 | 通俗题型指南、样篇按钮、范围/难度/批次/并发一站式配置 |
| 错题本 / 练习历史 / 原文对照 | 按错次排序的错题、逐次成绩曲线、中英对照与 brief 覆盖 |
| 生词本 | 收藏/已掌握、CSV 与 Markdown 导出 |
| 一键备份 | SQLite 在线备份 API 取一致性快照，归档排除 `.env`/`.env.web` |
| 导出中心 | 页面内单文件与整批 zip 下载，路径限制在 `output/exports/` 内 |
| 设置页 | 只读展示生效配置与密钥来源，一键测试模型连通性（脱敏回显） |
| 题目相似度去重 | 生成时实词 Jaccard 门禁（0.72／同篇 0.8）触发返工；`/review/similarity` 按 0.5 列出疑似对 |
| 抽样审阅 | 批任务收尾自动抽样进人工队列，通过/返工带批注；`review_samples` 表 |
| 可打印导出 | 错题本（含解析开关）与生词本（清单/自测卷）独立打印页，可直接存 PDF |
| 词汇记忆 | 五维分类 + 同根词族 + 记忆联想，Leitner 五盒间隔重复，四选一复习卡（词库不足降级自评） |
| 知识点汇总 | `/knowledge` 把阅读题与情境小说合成一本手册：词汇精讲、固定搭配、题干↔原文同义替换三个视图，服务端筛选分页、打印版与 Markdown 导出 |
| 词汇表契约 | 提示词写明字段与质量要求；质检拦下"只有 word、其余为空"的半截数据；旧数据按词形推断词性 |

## 6. 如何验证当前状态

```powershell
cd C:\Users\Lenovo\Desktop\ielts-reading-studio
python -m pip install -e ".[dev]"                        # 若尚未安装
python -m pytest -q                                      # 预期 724 passed（主项目 605 + 组件 119）
python -m ruff check app tests components/context-novel/src components/context-novel/tests   # 预期 All checks passed!
python -m compileall -q app components/context-novel/src # 语法编译检查
```

**不需要** `DEEPSEEK_API_KEY`；以上命令全部离线运行。

## 7. 关键裁定记录

| 主题 | 裁定 | 理由 |
|---|---|---|
| 估算调用次数 | 每单元基础 4 次、上限 10 次（作者返工 2 轮 → +4，考官返工 2 轮 → +2） | 方案原文写 3/7 次，未计入 brief 生成与文章复审；按设计规格的双智能体流程实际为 4 次基础调用 |
| 卷标题识别 | `卷二`、`第一卷` 均识别（中文章节号写在「卷」字之后） | 方案示例 `卷二 风起` 不含「第」字 |
| 低可信解析 | 置信度 < 0.6 或纯文本不足 2 个可用章节时 `chapters=[]`，并保留候选切分 | 设计规格要求「无法可靠识别边界时停止生成」，同时预览页仍可人工修正 |
| 编排位置 | 离线导入由 `app/planning/importer.py` 的 `CorpusImporter` 承担，任务 8 的服务层复用 | 任务 4 的集成测试需要 `service.import_source`，而该接口在任务 8 才出现 |
| 静态检查修复 | 一次性 `ruff --fix` 顺带修好任务 1/2 的既有告警，单独提交 | 任务 13 的验收要求 `ruff check` 全绿 |

## 8. 唯一剩余的外部验收门禁

1. **只有在用户本机配置好 `DEEPSEEK_API_KEY` 并指定中文源文件之后**，才执行一次真实样篇；
   样篇经你人工确认前，不会开启批量生成、不会替你批准任何单元。
2. 真实样篇完成后记录 API 返回的实际模型名、输入/输出 Token、三种导出路径和人工复核结论。
