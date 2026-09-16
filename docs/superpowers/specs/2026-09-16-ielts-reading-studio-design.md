# IELTS Reading Studio 设计规格

## 1. 产品目标

IELTS Reading Studio 是一个本地运行的 IELTS Academic Reading 内容生成与练习系统。它读取用户提供的中文 TXT、DOCX、EPUB 或 Markdown 文件，通过 DeepSeek API 将原文改写为忠于来源的英文 Academic Reading Passage，再由独立的考官智能体命题、审核并生成答案解析。

首版同时提供本地网页和命令行。网页用于导入文件、管理批量任务、在线答题和查看解析；命令行用于自动化、批量生成、恢复任务和导出。两种入口共用同一套流水线、数据模型、SQLite 状态和输出目录。

首版只支持 IELTS Academic Reading，不支持 General Training。默认难度为 Standard（约 Band 6.5–7.0），同时提供 Foundation（约 5.5–6.0）和 Advanced（约 7.5+）。难度标签表示生成目标，不宣称可替代官方评分或官方真题。

## 2. 核心原则

1. **忠于来源**：保留中文原文的核心事实、观点、因果关系和不确定性；允许重组、概括、改写和补充不含新事实的过渡论述。
2. **禁止伪造事实**：不得凭空增加具体数据、年份、研究机构、专家姓名、调查结果或引文。
3. **文章与命题分离**：定稿 Passage 之后才能生成题目，题目不得反向影响文章内容。
4. **双智能体独立职责**：作者智能体负责文章，考官智能体负责审稿与命题，使用不同的系统提示词和上下文。
5. **程序化门禁优先**：结构、编号、字数、题量、原文定位和填空限制等可确定检查由程序完成，不浪费模型调用。
6. **大任务可恢复**：每个生成单元独立落盘，支持暂停、恢复、失败隔离和按范围重试。
7. **先样篇后批量**：大型语料导入后默认只索引；先生成一篇样篇，经用户确认后才能开启批量。

## 3. 用户工作流

### 3.1 导入与索引

1. 用户在网页选择文件，或在 CLI 中传入本地路径。
2. 系统只读打开源文件，计算 SHA-256，并解析标题、自然段、章节边界和卷信息。
3. 系统创建语料库清单，显示章节数、总字符数、可生成单元数、预计 API 请求数和 Token 区间。
4. 导入不会自动触发 API 请求。
5. 用户可预览解析结果，并修正误识别的章节边界；修正结果保存为独立清单，不修改原文件。

支持的首版输入格式：

- TXT：按 BOM、UTF-8、GB18030 顺序探测编码。
- DOCX：读取非空正文段落和标题样式。
- EPUB：按 spine 顺序读取 XHTML 正文。
- Markdown：识别一级至三级标题和正文段落。

TXT 标题识别至少覆盖：中文数字和阿拉伯数字章节、`Chapter N`、卷、序章、终章和番外。无法可靠识别边界时，系统停止生成并要求用户在预览页确认或调整，不能把整个文件作为一次模型输入。

### 3.2 生成单元规划

一个生成单元对应一篇最终 Passage，并记录一个或多个来源章节。

- 默认最小来源长度为 800 个中文字符。
- 短章节按原顺序向后合并，直到达到最小长度；一次最多合并 4 章或 4,000 个中文字符，以先达到的上限为准。
- 合并到上限仍不足 800 字时允许生成，但标记 `limited_source=true`，并提示内容可能偏薄。
- 单个来源章节超过 6,000 个中文字符时，优先按标题和自然段边界切成 2,500–4,500 字的单元。
- 所有阈值均可在 `config.yaml` 调整，但清单会保存实际采用的配置快照。

### 3.3 样篇与批量

新语料库必须先生成一篇代表性样篇。用户确认样篇后，可选择章节范围、显式章节列表或全部任务加入队列。

- 默认批次大小：20 个生成单元。
- 默认同时处理 2 个生成单元；同一单元内部各阶段按顺序执行。
- 每个阶段完成后立即保存。
- 某个单元失败不会阻塞其他单元。
- “生成全部”必须再次显示范围、预计请求数和 Token 区间，并由用户明确确认。

## 4. 单篇内容规格

每个生成单元默认产出：

- 1 篇英文 Passage。
- 700–900 个英文词。
- 6–9 个以 A、B、C……编号的段落。
- 3 组题目。
- Foundation 共 10 题，Standard 共 12 题，Advanced 共 13 题。
- 完整答案、证据定位、中文解析和干扰项说明。
- 重点词汇表，包含词形、音标、词性、中文释义、常见搭配和原创例句。

首版题型池：

- Matching Headings
- True / False / Not Given
- Yes / No / Not Given
- Matching Information
- Multiple Choice
- Sentence Completion
- Summary Completion
- Short Answer

首版不生成 Diagram Label Completion，因为它还需要独立的图形生成和视觉校验。

默认题型组合如下，用户也可在任务创建时从题型池选择恰好 3 种：

- Foundation：Matching Headings 3 题、True/False/Not Given 3 题、Sentence Completion 4 题。
- Standard：Matching Headings 4 题、True/False/Not Given 4 题、Summary Completion 4 题。
- Advanced：Matching Information 4 题、Yes/No/Not Given 4 题、Multiple Choice 5 题。

考试视图不在正文显示中文释义。学习视图允许点击重点词查看释义、音标、搭配和例句。

## 5. 双智能体架构

### 5.1 作者智能体（Agent A）

职责：

1. 从来源文本生成结构化 `SourceBrief`，列出必须保留的事实、观点、因果关系、不确定性和禁止新增的具体事实类型。
2. 设计适合指定难度的英文篇章结构。
3. 生成 Passage，并为每一段记录其引用的来源信息 ID。
4. 根据考官智能体反馈，仅修改被指出的问题。

Agent A 不生成题目，也看不到考官为题目设计的预期答案。

### 5.2 考官智能体（Agent B）

职责分为两个动作，使用同一角色提示但分别调用：

1. `review_passage`：审核 Passage 的来源忠实度、Academic Reading 风格、逻辑、语言难度和潜在虚构事实。输出通过或结构化返工意见。
2. `build_assessment`：只在文章通过后，基于定稿 Passage 生成题目、答案、证据定位、中文解析和干扰项说明。

Agent B 不直接改写 Passage。发现文章问题时返回给 Agent A；发现题目问题时只重写受影响的题组。

### 5.3 模型配置

两个智能体通过同一 DeepSeek API 适配器调用，但具有独立的系统提示词、温度、最大输出 Token 和模型配置项。配置使用 `author_model` 与 `examiner_model` 字段，不在代码中绑定某个可能变化的具体模型名称。

### 5.4 返工上限

- Passage 审核失败：最多退回 Agent A 两次。
- 题目自动校验失败：最多退回 Agent B 两次。
- 超过上限后，任务进入 `needs_review`，保存最后结果与全部原因，不继续收费。
- 认证失败和余额不足立即停止整个队列；限流、网络错误和服务端错误采用带抖动的指数退避。

## 6. 数据模型

核心 Pydantic 模型：

### `Corpus`

- `id`
- `name`
- `source_path`
- `source_hash`
- `format`
- `encoding`
- `chapter_count`
- `created_at`
- `parser_version`

### `SourceChapter`

- `id`
- `ordinal`
- `volume_title`
- `chapter_title`
- `paragraphs`
- `character_count`
- `source_offsets`

### `GenerationUnit`

- `id`
- `corpus_id`
- `source_chapter_ids`
- `source_text_hash`
- `difficulty`
- `question_types`
- `config_snapshot`
- `prompt_version`
- `status`
- `limited_source`

### `SourceBrief`

- `core_facts`
- `core_claims`
- `causal_links`
- `uncertainties`
- `prohibited_inventions`
- `suggested_structure`

每条事实或观点都有稳定 ID，供 Passage 段落声明来源。

### `ReadingPassage`

- `title`
- `difficulty`
- `word_count`
- `paragraphs`
- `vocabulary`
- `source_coverage`
- `author_revision`

### `QuestionGroup`

- `type`
- `instructions`
- `word_limit`
- `options`
- `questions`

### `Question`

- `number`
- `prompt`
- `answer`
- `acceptable_answers`
- `evidence_paragraph`
- `evidence_quote`
- `chinese_explanation`
- `distractor_explanations`

### `ReadingPackage`

- `unit`
- `source_brief`
- `passage`
- `question_groups`
- `quality_report`
- `usage_records`

JSON 是所有导出的内容源。HTML、DOCX 和练习册都从已通过校验的 `ReadingPackage` 渲染。

## 7. 状态机与任务编排

生成单元状态：

```text
indexed
→ author_generating
→ passage_reviewing
→ examiner_generating
→ validating
→ completed
```

异常状态：

- `author_revision_required`
- `examiner_revision_required`
- `needs_review`
- `failed`
- `paused`
- `cancelled`

状态写入 SQLite。模型原始响应、规范化 JSON、质量报告和使用量记录写入任务目录。进程重启时，`running` 类状态回退到相应阶段的可恢复状态；已成功且缓存键相同的阶段不会重复调用。

缓存键由以下字段组成：

- 来源文本哈希
- 难度
- 题型与题量
- 模型配置
- Prompt 版本
- 关键生成参数

## 8. 程序化质量门禁

### 8.1 Passage 校验

- 字数和段落数量符合所选规格。
- 段落标签连续且唯一。
- 每段存在来源信息映射。
- `SourceBrief` 中的核心事实和观点均被覆盖，或明确记录合理省略原因。
- 新出现的数字、年份、机构、人名和引用必须能在来源中找到；否则拒绝。
- 难度指标处于目标范围；首版使用句长、词频层级、词汇多样性和连接结构组合指标，不将其冒充官方 Band 评分。
- 不包含 Markdown 围栏、模型解释或未完成占位符。

### 8.2 题目校验

- 题组数、题型和总题量正确。
- 题号连续且唯一。
- 每题都有答案、证据段落、证据摘录和中文解析。对于 Not Given，证据摘录保存最接近题干主题的上下文，解析必须明确指出 Passage 缺少哪一项判定所需信息。
- 证据摘录必须是 Passage 的真实子串，段落标签必须匹配。
- Sentence/Summary Completion 与 Short Answer 的答案必须来自 Passage，并满足题组字数限制。
- Matching Headings 的标题数量必须多于待匹配段落数，且标题不可重复使用。
- Multiple Choice 必须包含要求数量的选项和唯一正确答案。
- True/False/Not Given 与 Yes/No/Not Given 必须包含考官的语义判定理由；程序检查证据完整性，语义最终由 Agent B 审核。
- 题干不得直接泄露答案，不得引用答案解析中的信息。

### 8.3 质量报告

每篇保存：

- 来源覆盖率
- Passage 字数、段落数和难度指标
- 词汇覆盖与重复情况
- 题型和题量
- 每题证据检查结果
- 返工次数和原因
- 每阶段输入/输出 Token、耗时、重试次数和错误类型

语料库汇总报告每完成 20 篇更新一次，包含完成率、失败率、平均调用次数、难度分布和题型分布。

## 9. 本地网页

首版采用 FastAPI、Jinja 模板和少量原生 JavaScript，不引入独立前端构建链。

页面：

1. **语料库列表**：查看导入文件、章节数、进度和最近任务。
2. **导入与解析预览**：选择文件、查看章节边界、调整错误边界。
3. **生成配置**：选择范围、难度、题型、批次大小和并发数，查看估算。
4. **任务监控**：展示每个单元当前阶段、返工原因、Token 用量，并支持暂停、继续和重试。
5. **在线练习**：计时、答题、交卷和本地评分；交卷前不显示答案。
6. **学习解析**：显示答案、证据定位、中文解析、干扰项说明和可点击词汇。
7. **导出中心**：下载单篇或按批次导出的 DOCX、独立 HTML 和 JSON。

Web 服务默认只监听 `127.0.0.1`。首版不实现账号、多用户、云同步和公网部署。

## 10. 命令行

CLI 与网页调用同一服务层。计划命令：

```powershell
ielts-reading import <path>
ielts-reading inspect <corpus-id>
ielts-reading estimate <corpus-id> --range 1-20 --level standard
ielts-reading sample <corpus-id> --level standard
ielts-reading generate <corpus-id> --range 1-20 --level standard
ielts-reading resume <job-id>
ielts-reading retry <job-id> --failed-only
ielts-reading export <job-id> --format html|docx|json
ielts-reading serve
```

`estimate` 和导入解析不建立 DeepSeek 客户端，也不产生 API 费用。

## 11. 存储与输出

SQLite 是任务状态的权威来源。文件系统保存可移植产物和审计材料。

```text
output/
└── <corpus-slug>/
    ├── manifest.json
    ├── source_units/
    ├── packages/
    ├── html/
    ├── docx/
    ├── reports/
    ├── raw_responses/
    ├── failed/
    └── exports/
```

每篇单独写入并使用临时文件加原子替换。练习册默认每 20 篇一卷，可配置为 20–50 篇；不生成包含全部两千篇内容的单个 Word 文件。

## 12. 安全与成本控制

- DeepSeek API Key 只从环境变量或被 Git 忽略的 `.env` 读取。
- `.env.example` 只包含变量名，不包含真实值。
- 日志、异常、原始响应元数据和网页错误信息统一遮盖疑似密钥。
- 原始输入文件只读，不原地修改。
- 认证失败或余额不足立即停止队列。
- 默认全局生成并发为 2，可配置但设置安全上限。
- 所有批量操作先显示范围和估算；全量操作需要明确确认。
- 可配置每次运行的最大单元数、最大预计 Token 和最大连续失败数。

成本估算以模型输入输出 Token 的上下界呈现。若用户未在配置中提供价格，不显示虚假金额，只显示预计 Token 和调用次数。

## 13. 技术结构

```text
ielts-reading-studio/
├── app/
│   ├── agents/
│   ├── pipeline/
│   ├── parsers/
│   ├── validators/
│   ├── exporters/
│   ├── storage/
│   ├── web/
│   ├── cli.py
│   ├── config.py
│   └── models.py
├── templates/
├── static/
├── tests/
├── input/
├── output/
├── config.yaml
├── .env.example
├── pyproject.toml
└── README.md
```

建议依赖：Python 3.12、Pydantic、FastAPI、Uvicorn、Jinja2、Typer、SQLAlchemy、OpenAI 兼容客户端、python-docx、EbookLib、BeautifulSoup、PyYAML、python-dotenv 和 pytest。具体版本在实施时锁定并通过兼容性测试确认。

## 14. 测试策略

### 单元测试

- TXT/DOCX/EPUB/Markdown 解析和章节边界。
- 短章节合并与长章节切分。
- Pydantic 数据模型与状态迁移。
- 缓存键、原子写入和恢复。
- Passage 与题目程序化校验。
- HTML、DOCX 和 JSON 导出一致性。
- 密钥与错误信息脱敏。

### 集成测试

- 使用伪造 DeepSeek 客户端覆盖正常生成、坏 JSON、空响应、截断、429、5xx、超时和认证失败。
- 覆盖 Agent B 退回 Agent A、题组局部返工、超过返工上限和进程重启恢复。
- 覆盖 2,000 章清单的索引、分页和队列调度，不进行真实模型调用。

### 真实样篇验收

在用户本机配置 API Key 后，仅生成一个样篇。验收项：

- 文章忠于来源且无虚构具体事实。
- 英文自然，结构接近 Academic Reading。
- 难度与目标级别相符。
- 题目可由 Passage 唯一作答。
- 答案定位和中文解析准确。
- 网页练习、评分和三种导出可用。

样篇通过前，不开启批量确认配置。

## 15. 实施阶段

1. 项目骨架、配置、数据模型和 DeepSeek 适配器。
2. 四种输入解析、章节索引、生成单元规划和大语料清单。
3. Agent A、Agent B、状态机、缓存、返工与质量门禁。
4. CLI 全流程、估算、样篇确认和批量恢复。
5. HTML、DOCX、JSON 导出。
6. FastAPI 本地网页、任务监控和在线练习。
7. 真实样篇验收、文档和批量安全检查。

每一阶段都要求对应自动化测试通过后再进入下一阶段。

## 16. 首版非目标

- IELTS General Training。
- Diagram Label Completion 和自动生成插图。
- 官方 Band 评分承诺。
- 用户账号、多人协作、云部署或跨设备同步。
- 自动抓取互联网资料来补充原文。
- 一次生成三篇、40 题的完整套卷；首版只保留未来组合接口。
- 把两千篇内容导出为一个超大 DOCX。

## 17. 完成标准

首版完成必须同时满足：

1. 能可靠导入四种格式，并在不调用 API 的情况下索引 2,000 章以上 TXT。
2. 能生成 Foundation、Standard 和 Advanced 单篇 Package。
3. Agent A 与 Agent B 职责、提示词和调用记录相互独立。
4. 不合格结果能定向返工，达到上限后安全停止。
5. 单篇结果可在线练习并导出 HTML、DOCX 和 JSON。
6. 批量任务可暂停、恢复、跳过已成功单元并重试失败单元。
7. API Key 不进入仓库、日志和产物。
8. 自动化测试覆盖核心解析、生成编排、校验、恢复和导出路径。
9. 真实样篇经用户人工确认后，系统才允许开启批量生成。
