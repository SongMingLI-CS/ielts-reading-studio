# IELTS Learning Studio

IELTS Learning Studio 把原来的两个 IELTS 项目合并为一个私有网站：

- **IELTS Academic Reading**：把中文素材生成英文 Passage、题目和逐题解析，支持在线练习、成绩记录与导出。
- **雅思词汇情境小说**：保留中文小说故事线，在语境中嵌入 B1–C1 词汇，当前内置 6,186 词全局词库，并支持断点续跑、HTML、DOCX、TXT 和 XLSX。
- **IELTS 写作评估**：网页提交 Task 1 / Task 2，按四项标准返回结构化 Band 与反馈，并保存评估记录和模型用量；也可直接调用 API。

网站共用一个登录入口和一套响应式界面，两条流水线的数据目录与状态库彼此隔离。情境小说原项目以 `components/context-novel/` 组件保留在仓库中。

难度标签是生成目标，不是官方 Band 评分；项目不声称生成内容等同于官方 IELTS 真题。

## 安装

需要 Python 3.12 或 3.13。依赖版本锁定在 `uv.lock`，可重复安装：

```bash
git clone https://github.com/SongMingLI-CS/ielts-reading-studio.git
cd ielts-reading-studio
uv sync --frozen --extra dev      # 按 uv.lock 精确建立 .venv
cp .env.example .env
```

没有 uv 时也可以只用 pip（按 `pyproject.toml` 的版本范围安装，不做精确锁定）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

修改依赖后请运行 `uv lock`，再 `uv sync --extra dev`，并把 `uv.lock` 一起提交；CI 使用 `uv sync --frozen`，锁定文件与声明不一致时会直接失败。

仅在准备生成真实样篇时，才在未被 Git 追踪的 `.env` 中填写：

```dotenv
DEEPSEEK_API_KEY=你的本地密钥
```

解析、导入、检查、估算和离线验证都不需要密钥，也不会创建 DeepSeek 客户端。

## 离线导入与估算

```powershell
ielts-reading import .\input\book.txt --level standard
ielts-reading inspect <corpus-id>
ielts-reading estimate <corpus-id> --range 1-20 --level standard
```

`--range` 接受 `1-20` 或 `1,3,8-12`。估算显示请求数和 Token 上下界；未配置单价时不会虚构金额。低可信章节边界会停止生成并显示诊断，不会把整本文件交给模型。

## 样篇门禁与批量生成

真实调用顺序必须是：

```powershell
ielts-reading sample <corpus-id> --level standard
# 人工检查 output\packages、HTML 和 DOCX 后：
ielts-reading approve-sample <corpus-id> <sample-unit-id>
ielts-reading generate <corpus-id> --range 2-21 --level standard
```

样篇未完成时不能批准；未明确批准样篇时不能创建批量任务。`generate --all` 会再次显示范围和估算，并要求逐字输入 `确认全部生成`。`--yes` 只用于已经完成外部审批的非交互脚本。

恢复和失败重试：

```powershell
ielts-reading resume <job-id>
ielts-reading retry <job-id> --failed-only
```

暂停与取消可在本地网页的任务监控页执行。阶段结果、缓存键、原始响应元数据和 Token 用量逐阶段落盘；重启后不会重复调用已完成且缓存键一致的阶段。

## 网页服务

```powershell
ielts-reading serve
```

默认地址是 `http://127.0.0.1:8000`。首页提供“阅读练习”和“情境小说”两个入口。若要监听局域网或公网地址，必须先通过环境变量配置独立的网站账号和密码：

```dotenv
IELTS_WEB_USERNAME=reader
IELTS_WEB_PASSWORD=请使用独立的强密码
```

然后运行 `ielts-reading serve --host 0.0.0.0 --port 8766`。远程监听但未配置完整凭据时，程序会拒绝启动；网站密码也不会从 YAML 读取。公网生产环境必须在反向代理上配置 HTTPS：HTTP Basic Auth 不加密流量，而且浏览器在非安全上下文（非 https、非 localhost）不提供 `crypto.randomUUID()` 等 Web API。`serve` 已开启 `proxy_headers`，反代请转发 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`；完整 nginx 示例、资源缓存与单用户限制见[运维指南](docs/operator-guide.md)第 9 节。随附的 systemd 单元则把应用绑在 `127.0.0.1:8768`，由 Caddy 单元在公网端口 8766 上终止 TLS（自签、按 IP 访问）：这种情况下 `serve` 的启动闸门不触发，改由 `scripts/deploy.sh --check` 用同一套规则强制检查。网页提供：

- 安全上传、章节边界预览和独立修正记录；
- 生成范围、难度、三种题型、批次和并发配置；
- 样篇批准、任务暂停/继续/失败重试与状态轮询；
- 在线计时答题、本地精确评分、证据与中文解析；
- 写作评估：提交 Task 1 / Task 2 作文，查看四项 Band、具体反馈、改进建议与可选范文；
- 错题本、练习历史、原文对照，以及错题/生词本的打印版（可直接存 PDF）；
- 导出中心：单篇或成批导出 JSON、HTML、DOCX，并支持在页面内下载单文件或整包 zip；
- 抽样审阅：批任务自动抽篇进人工队列（通过/返工 + 批注），并提供全库相似题目扫描；
- 词汇记忆：按词性、复现频率、来源、难度、同根词族分类，Leitner 盒子间隔重复复习；
- 设置页：显示当前生效配置、密钥来源（不回显密钥）与数据位置，并可一键测试模型连通性。
- 情境小说上传、章节识别、离线 Token 估算、样章生成门禁和成品下载。
- 情境小说按书归档：源文件存在 `input/context-novel/library/<书 id>/source.<ext>`（按 sha256 去重、
  永不删除），成品在 `output/context-novel/books/<书 id>/`，索引是 `input/context-novel/books.json`
  （书名、文件名、字节数、章节数、导入时间与当前生成目标）。导入新书不再覆盖上一本：页面顶部
  可以换一本看（`?book=`）、指定「生成目标」，章节列表按每页 20 章分页。旧布局的数据在第一次
  打开页面时迁移一次，只按章节标题归属、只复制不删除，结果写在 `output/context-novel/migration_log.json`。

## 写作评估网页与 API

导航栏的「写作评估」（`/writing`）提供完整提交与结果页面，也可以直接调用以下接口。

`POST /api/writing/evaluate` 接收 JSON：

```json
{
  "task_type": "task_2",
  "question": "Discuss both views and give your opinion.",
  "essay": "Your complete essay...",
  "title": "Optional title",
  "include_sample_answer": false
}
```

响应包含总分、四项评分与反馈、优点和改进建议。Task 1 返回
`task_achievement`，Task 2 返回 `task_response`；其余三项均为
`coherence_and_cohesion`、`lexical_resource` 和
`grammatical_range_and_accuracy`。每次成功评估都会写入 SQLite 的
`writing_evaluations` 表。该接口会调用配置的 `writing_model`，因此需要
`DEEPSEEK_API_KEY`；`writing_max_output_tokens` 控制单次最大输出。

## 导出中心

```powershell
ielts-reading export <job-id> --format json
ielts-reading export <job-id> --format html,docx,json --workbook-size 20
```

HTML 是不依赖网络的单文件，交卷前界面不暴露答案。DOCX 单篇可独立导出；多篇练习册每卷必须为 20–50 篇，不会把两千篇合成一个超大文件。所有格式都从同一份已校验 `ReadingPackage` JSON 渲染。

网页的「导出中心」（`/exports`）做同一件事，但不需要命令行：列出已完成且质检通过的篇目（显示文章标题而非裸 ID）、解释三种格式各自适合谁，生成后把文件列在页面上，可逐个下载或整批打包成 zip。文件写入 `<output_dir>/exports/<批次>/`。下载入口只允许读取该目录内的文件，`..` 之类的路径穿越会返回 404。注意网页里没有「删除导出」按钮，需要清理时用 SSH 删除对应批次目录。

## 设置页

网页的「设置」（`/settings`）是只读的，用来回答「现在到底用的是哪个模型、密钥从哪来、钱花在哪」。它显示出题/审校模型、API 地址、批次与并发、返工与限额、切分阈值、配置文件与数据目录，并给出每个开关的影响说明；密钥只显示「已配置/未配置」和来源（环境变量、`.env` 或服务器 `.env.web`），不会回显内容。

页面顶部的「测试模型连接」按钮会发起一次极小的真实请求（几十 token），用来验证密钥、余额和模型名是否可用，结果直接显示在页面上，失败原因经脱敏后回显。网页不会回写 `config.yaml`：要改配置请编辑服务器上的文件并重启服务（`sudo systemctl restart ielts-reading-studio`）。

## 题目相似度去重

生成的练习越多，"换汤不换药"的题越难靠肉眼发现。去重分两层：

- **生成时的硬门禁**：每道新题都与"已完成篇目的题干索引"比较实词重合度（Jaccard），超过 `question_duplicate_threshold`（默认 0.72）即判为重复，进入返工；同篇内部用更严格的 0.8。改不动的单元转人工审阅，不会静默放行。
- **审阅页的宽松扫描**：`/review/similarity` 用 `question_report_threshold`（默认 0.5）列出全库疑似重复对，逐对展示题干与答案，由人判断是否真的重复。

短题干（如 "Paragraph A"）不参与判重：信息量不足时误判要花钱重生成，漏判由审阅页兜底。索引按"已完成篇目集合"缓存，批任务里新完成的篇目会立即入库参与后续比对。

## 抽样审阅

机器质检只能挡住规则内的错误。一批任务跑完后会自动抽样（`review_sample_rate`，默认 10%，至少 `review_sample_min` 篇）进入 `/review` 队列：

- 每张卡显示篇号、标题、题量、质检结论、随机抽样原因，并直接跳到该篇的文章与答案页；
- 决定只有两个：**通过** 或 **需返工**，都可以写批注；已人工审过的不再被重新抽样覆盖；
- 抽样用 `job_id` 作随机种子，同一次抽样结果稳定；页面上另有「再抽一批样本」可强制重抽。

## 词汇记忆

`/vocabulary` 是词汇板块（生词本仍在练习中心的 `/practice/vocabulary`，负责收藏与导出）：

- **分类**：按词性、复现频率（≥3 篇 / 2 篇 / 1 篇）、来源篇目、难度、同根词族五组浏览；同根词族由本地词干规则自动归并（如 conserve / conservation），不查外部词典；
- **记忆联想**：每个词给出同根词与同篇共现词，作为记忆挂钩；
- **间隔重复**：Leitner 五盒，间隔 1 / 2 / 4 / 8 / 32 天。答对进下一盒，答错退回第一盒，"有点模糊"停在原盒；复习卡是四选一（干扰项取其它词的释义），词库不够时自动降级为自评模式；
- **打印版**：`/practice/vocabulary/print` 支持清单与自测卷两种模式（自测卷只留横线，答案在末页）。

错题本的打印版在 `/practice/mistakes/print`，可选含解析/不含解析、按题型筛选，默认最多 60 道。两个打印页都不套站点外壳，直接打印或"另存为 PDF"即可。

## 知识点汇总（`/knowledge`）

把已经生成的**阅读练习**与**情境小说**重新整理成一份可读的手册——不是背单词工具，
而是"学过什么、怎么用"的清单。三个视图：

- **词汇精讲**：词条按字母/难度/复现次数排序，带音标、词性、难度等级、中文释义、固定搭配与例句；
  例句里讲的那个词会被高亮。同一个词在阅读与小说里都出现时合并为一条，但分别记录"阅读 N 篇 / 小说 M 次"。
- **固定搭配**：把两侧的搭配摊平成一张张卡片（如 `abandon ship`、`adjust to`），标注所属词与例句。
- **同义替换**：阅读题专属。每道题给出"**题干怎么说 ↔ 原文怎么说**"的并排对照，
  题干侧的替换措辞与原文侧的对应措辞分别高亮，并列出同根词形变化（conserve ≈ conserving）。
  对齐是本地词干比对算出来的，不查词典、不调用模型。

数据来源与口径：

| 来源 | 取什么 | 说明 |
|---|---|---|
| `output/packages/*.json` | `passage.vocabulary`、每题的 `prompt` 与 `evidence_quote` | 只读已完成的篇目 |
| `output/context-novel/books/<书 id>/glossary.json` | 术语库（含 CEFR、音标、搭配、例句、首末章、复现次数） | 按书隔离；当前书未生成时页面会提示，不影响阅读侧内容 |
| `output/context-novel/books/<书 id>/chapter_json/*.json` | 每段的"中文语境 → 英文表达"替换对 | 展示为「中英语境」，说明这个表达是在什么语境里用的 |

筛选、排序、分页都在服务端完成，所有状态写在地址里（可收藏、可分享）：
来源（仅阅读 / 仅小说 / 两边都出现）、难度（B1/B2/C1）、类型（单词 / 固定搭配 / 短语动词）、
词性、只看有搭配、只看复现 ≥2、关键词搜索（匹配词、中文释义、搭配、例句）。

导出：`/knowledge/print` 是打印版（可直接存 PDF），`/knowledge/export.md` 导出当前筛选结果的 Markdown。

## 配置与安全上限

`config.yaml` 中可设置模型名、输出路径、并发、批次、来源切分阈值、单次最大单元数、最大预计 Token 和连续失败上限。模型名不写死在代码中。API Key 只从环境变量或项目旁的 `.env` 读取，YAML 中出现的密钥会被忽略。

认证失败或余额不足会阻断队列；限流、超时和服务端错误最多按约 1、2、4 秒加抖动重试。文章返工和题目返工分别最多两轮，超过后进入 `needs_review`，不继续收费。

## 安全与公开部署

完整威胁模型、控制清单与剩余风险见 [docs/security.md](docs/security.md)。要点：

- **会话**：服务端会话表 + `HttpOnly` / `SameSite=Lax` Cookie，登录后轮换会话 ID，退出即时失效，
  空闲 120 分钟 / 绝对 24 小时过期；数据库只保存会话 ID 的 HMAC。
- **CSRF**：所有 POST/PUT/PATCH/DELETE 都要求 CSRF 令牌（HTML 表单隐藏字段或 `X-CSRF-Token` 头），
  令牌与会话绑定并用常量时间比较；`Origin`/`Referer` 同源校验作为纵深防御。
- **限速**：登录失败（同时按身份与来源地址）与付费/敏感接口使用 SQLite 持久化固定窗口计数，
  超限返回 429 与 `Retry-After`；只有配置了可信代理才会解析 `X-Forwarded-For`。
- **预算**：请求体、上传大小、Prompt 长度、输出 Token、重试次数、超时与批次预估成本全部由服务端强制裁剪。
- **响应头**：CSP（脚本使用每响应 nonce，无 `unsafe-inline`/`unsafe-eval`）、`nosniff`、
  `frame-ancestors 'none'`、`Referrer-Policy`、`Permissions-Policy`；私人页面 `no-store`，静态资源可缓存。
- **文件**：上传按扩展名白名单 + 容器格式校验，服务端生成文件名；下载路径经 `resolve()` 限定在导出/产物目录内。

公网部署必须在环境变量（systemd 的 `.env.web`）中提供：

```dotenv
IELTS_WEB_USERNAME=reader
IELTS_WEB_PASSWORD=至少12字符的强口令
IELTS_WEB_SESSION_SECRET=openssl rand -hex 32 生成的随机值
IELTS_WEB_FORCE_HTTPS=1
IELTS_WEB_TRUSTED_PROXIES=127.0.0.1
```

配置不完整时 `ielts-reading serve --host 0.0.0.0` 会**拒绝启动**并列出原因。

口令长度下限默认 12 字符，可用 `IELTS_WEB_PASSWORD_MIN_LENGTH` 显式放宽（最低 8，低于 8 直接被判为配置错误）。这是一处**有意的、可见的**风险取舍，而且只放宽这一项：弱口令阻断列表、登录失败限速、CSRF 与其余闸门都不随之改变。单用户公开部署（服务器的 `.env.web`）当前把下限设为 10 以匹配既有口令；口令轮换后应改回默认 12。判定逻辑与剩余风险见 [docs/security.md](docs/security.md) 第 5 节。

程序化调用需要两步（JSON API 也要 CSRF）：

```bash
# 1. 取得会话 Cookie 与令牌
curl -s -u reader:password -c cookies.txt http://127.0.0.1:8000/api/csrf-token
# {"token":"..."}
# 2. 带上 Cookie 与令牌调用
curl -s -b cookies.txt -H "X-CSRF-Token: <token>" -H 'Content-Type: application/json' \
  -d '{"task_type":"task_2","question":"...","essay":"..."}' \
  http://127.0.0.1:8000/api/writing/evaluate
```

## 后台任务与 worker

网页不再自己跑生成任务：提交请求只是**入队**，由独立的 worker 进程认领并执行。这样重启网页、部署新版本或机器重启都不会丢掉一个跑了 40 分钟的批任务。

```bash
ielts-reading worker              # 常驻：轮询队列、持有租约、按单元边界恢复
ielts-reading worker --once       # 只处理当前队列里的作业（适合调试与测试）
ielts-reading worker --max-jobs 1 --poll-interval 1
```

工作机制：

- **认领是原子的**：`UPDATE jobs SET status='running' WHERE id=? AND status='queued'` 这样的 compare-and-set 保证一个作业只能被一个 worker 拿到；并发认领时只有一个成功。
- **租约与心跳**：认领时写入 `lease_expires_at`，worker 在后台线程按租约的 1/4 间隔续租。worker 崩溃后租约过期，另一个 worker 会把它安全地重新排队（`attempts` 递增），连续 3 次失败才标记为 `failed`，`error_code=worker_lease_expired`。
- **不重复付费**：单元状态、阶段尝试与缓存键都在 SQLite 里，重新认领只会继续没做完的阶段；已完成且缓存键一致的阶段不会再次调用模型。恢复流程与 `ielts-reading resume` 完全相同。
- **暂停 / 继续 / 取消**：`请求暂停` 让 runner 在下一个单元边界停下（在途阶段先落盘），`继续` 把作业重新入队并释放中断的单元，`取消` 是终态。
- **重复提交保护**：网页提交带幂等键（批任务由语料 + 范围 + 难度 + 题型 + 批次/并发算出）。同一个请求在作业仍在排队或运行时只会复用同一个作业；上一个作业结束后键会被释放，所以重新生成仍然可以正常提交。
- **全局上限**：`max_active_jobs`（默认 2）限制排队中与运行中的作业总数，超出时提交返回 429 并说明原因；单个 worker 进程同一时刻只跑一个作业，配合 `concurrency` 形成全局模型调用上限。
- **任务页**会显示队列状态：`等待 worker`、`worker 处理中`、`中断待恢复`（租约已过期）、`已暂停`、`已阻塞`、失败原因与错误码。

生产环境用 `deploy/ielts-reading-studio-worker.service` 常驻运行 worker（已经和网页服务一样开了 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict` 与 `ReadWritePaths` 加固）：

```bash
sudo cp deploy/ielts-reading-studio-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ielts-reading-studio-worker
journalctl -u ielts-reading-studio-worker -f
```

`scripts/deploy.sh` 会在部署与回滚时一并重启 worker（未安装则跳过）。没有 worker 在跑时，提交的作业会一直停在「等待 worker」——这是最容易排查的现象。

## 数据库迁移

`output/state.db` 的 schema 由 Alembic 管理，迁移脚本在 `migrations/versions/`：

```bash
ielts-reading migrate --check     # 只报告版本；落后时退出码 2（可放进部署前置检查）
ielts-reading migrate             # 升级到最新版本
```

- **全新安装**：`migrate` 建立全部表和索引。
- **迁移前建立的旧库**（有应用表、没有 `alembic_version`）：只会被**标记**为基线版本 `0001`，不会重建表、不会改动任何一行数据。
- **服务启动时自动迁移**（`serve`、网页、CLI 都走同一条路径）。迁移失败会带明确中文原因终止启动，不会带着未知 schema 继续运行。
- **版本比代码新**（例如回退到旧版本代码）会被拒绝，而不是静默降级。
- 回滚先备份数据库，再执行：

```bash
cp output/state.db output/state.db.before-rollback
IELTS_DATABASE_URL="sqlite+pysqlite:///$PWD/output/state.db" .venv/bin/alembic downgrade -1
```

`migrations/versions/0001_baseline_schema.py` 之后新增的版本都必须写 `downgrade()`，否则回滚无效。

## 验证

一个命令跑完全部离线门禁：两个测试套件（`tests/` 与 `components/context-novel/tests/`）、
`app/` 与组件源码的字节编译、四处 Ruff 检查、CLI 帮助烟雾测试。不需要 API Key，
也不会访问网络——`tests/conftest.py` 会直接拦截任何非回环连接与 DNS 解析。

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

Linux / macOS（服务器上用这个）：

```bash
scripts/verify.sh
```

两个脚本执行同样的检查。`verify.sh` 默认使用项目里的 `.venv/bin/python`（也就是 systemd 启动服务用的同一个解释器），
可用 `PYTHON=/path/to/python` 或 `IELTS_VERIFY_PYTHON=` 覆盖。

当前基线：**723 passed**（主项目 604 + 情境小说组件 119）、Ruff 全仓零告警。CI
（`.github/workflows/ci.yml`）在 Python 3.12 与 3.13 上执行同一条命令，并且不配置任何密钥。
更详细的恢复、备份和状态说明见 [运维指南](docs/operator-guide.md)。
