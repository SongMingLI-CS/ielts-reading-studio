# IELTS Reading Studio 完成审计

审计日期：2026-09-17

审计范围以 `docs/superpowers/plans/2026-09-16-ielts-reading-studio.md`、设计规格、`IMPLEMENTATION-STATUS.md` 和当前 `main` 工作树为准。结论不依赖历史摘要；以下证据均来自当前代码、测试和运行结果。

## 结论

任务 1–13 的代码、离线行为、测试、CLI、本地网页、导出与运维文档均已交付。真实 DeepSeek 样篇属于条件式外部验收：只有用户在本项目配置本地密钥并指定源文件后才能执行；当前没有密钥授权或指定源文件，因此没有进行真实调用，也没有产生 Token 费用。

## 逐任务证据

1. **项目骨架、配置与领域模型**
   - `app/config.py` 只从环境或 `.env` 读取密钥，并统一脱敏。
   - `app/models.py` 覆盖全部领域类型、三种不同题型、连续题号、唯一段落标签与唯一 Brief item ID。
   - 证据：`tests/test_config.py`、`tests/test_models.py`。

2. **SQLite、原子产物与缓存**
   - SQLite 开启 WAL、外键、busy timeout；单元通过 compare-and-set 认领。
   - 阶段尝试、缓存复用、并发缓存竞态收口、使用量与原子 JSON 均有持久化。
   - 证据：`tests/storage/`。

3. **四格式解析与可靠章节边界**
   - TXT、DOCX、EPUB、Markdown 均有解析器；低可信输入不生成单元。
   - Web 边界修正支持 rename、merge_next、split_before_paragraph，原子替换独立清单与数据库索引，不修改原文件，开始生成后冻结。
   - 证据：`tests/parsers/`、`tests/planning/test_boundaries.py`、`tests/web/test_corpora.py`。

4. **生成单元、清单与离线估算**
   - 短章合并、长章切分、稳定 ID、精确来源跨度和 4–10 次调用估算已实现。
   - 2,000 章集成测试拦截 socket，并验证导入不新增 Agent 模块。
   - 证据：`tests/planning/`、`tests/integration/test_large_manifest.py`。

5. **DeepSeek JSON Provider**
   - 使用 OpenAI 兼容 Chat Completions、JSON mode、可配置模型和 base URL。
   - 401/402 不重试；429、超时、连接与 5xx 使用约 1/2/4 秒加抖动的三次有界重试；截断在 JSON 解析前拒绝。
   - 证据：`tests/agents/test_deepseek.py`、`tests/integration/test_failure_matrix.py`。

6. **独立作者与考官 Agent**
   - Agent A 只处理 brief、Passage 与文章修订；Agent B 独立审稿、命题和题组局部修复。
   - 两者使用不同模型字段、角色提示、阶段名与用量记录。命题只接收冻结 Passage。
   - 难度、修订轮次和响应 Schema 在返回前强校验。
   - 证据：`tests/agents/test_author.py`、`tests/agents/test_examiner.py`。

7. **程序化质量门禁**
   - Passage 覆盖字数、段落、连续标签、来源映射、Brief 覆盖、具体事实、围栏/占位符和透明难度指标。
   - 题目覆盖总量、编号、证据子串、Not Given 理由、填空字数、Matching Headings、Multiple Choice 与答案泄露。
   - 证据：`tests/validators/`。

8. **可恢复流水线**
   - 单元严格执行 brief → Passage → 门禁 → 审稿 → 命题 → 门禁 → Package。
   - 文章问题只退回作者，题目问题只退回失败题组；上限分别为两轮。
   - 批量使用滚动式有界并发，支持暂停、恢复、失败隔离、认证/计费阻断、Token 上限和汇总报告。
   - 崩溃恢复测试证明冻结 Passage 可复用，不重复调用作者。
   - 证据：`tests/pipeline/`、`tests/integration/test_resume.py`。

9. **JSON、HTML 与 DOCX 导出**
   - 只导出 completed 且校验通过的 canonical Package。
   - HTML 是离线单文件，提交前 UI 不显示答案；所有模型文本转义。
   - DOCX 包含文章、题目、答案解析和词汇；练习册强制每卷 20–50 篇，51 篇拆为 20/20/11。
   - 证据：`tests/exporters/`。

10. **Typer CLI 与确认门禁**
    - 提供 import、inspect、estimate、sample、approve-sample、generate、resume、retry、export、serve。
    - 导入与估算不需要 Key；`generate --all` 要求字面确认；批量设置必须匹配批准样篇。
    - 模块入口和安装后的 `ielts-reading` 控制台入口均通过烟雾检查。
    - 证据：`tests/cli/`。

11. **FastAPI 管理页面**
    - 上传只信任白名单扩展，流式限制 250 MB，并保存到 `input/<corpus-id>/source.<ext>`。
    - 提供分页预览、结构化边界修正、配置与估算、样篇审批、任务监控、暂停/继续/失败重试/取消。
    - 服务默认且通过 CLI 强制绑定 `127.0.0.1`。
    - 证据：`tests/web/test_corpora.py`、`tests/web/test_jobs.py`，以及本地 Uvicorn 200 响应烟雾检查。

12. **浏览器练习、评分、解析与导出中心**
    - 练习页不渲染答案；提交后才返回正确性、答案、证据和解析。
    - 评分执行 Unicode、大小写和空白规范化，不使用模糊匹配；多选 API 使用无序集合比较。
    - 学习页高亮证据段落并展示词汇；导出中心拒绝未完成或未校验单元。
    - 证据：`tests/web/test_practice.py`、`tests/web/test_exports.py`。

13. **端到端加固与运维**
    - 假 Provider 覆盖 Foundation、Standard、Advanced，及 import → sample → approve → batch → JSON/HTML/DOCX 全流程。
    - 失败矩阵覆盖 401、402、429、500、超时、空内容、截断、坏 JSON、Agent Schema、审稿拒绝、题组返工和上限停止。
    - 密钥哨兵扫描异常、日志、SQLite、raw response 元数据和生成产物；只有临时 `.env` 可包含哨兵。
    - `scripts/verify.ps1`、`README.md` 与 `docs/operator-guide.md` 已交付。

## 全局约束审计

- 仅支持 IELTS Academic Reading，文档明确不宣称官方评分或真题等价。
- 导入与估算路径不构造 DeepSeek 客户端。
- API Key 不进入 YAML、Git、日志、SQLite 或产物。
- Passage 冻结后才命题；作者看不到题目答案。
- 作者/考官返工硬上限均为 2。
- 默认批次 20、并发 2，且有单次单元、Token 和连续失败上限。
- 未批准样篇无法创建批量任务，批准内容绑定难度和三种题型。
- HTML、DOCX 均从 canonical JSON Package 渲染。
- 不存在整套 2,000 篇单一 DOCX 导出路径。

## 最终验证命令

```text
python -m pytest -q
python -m compileall -q app
python -m ruff check app tests
python -m app.cli --help
ielts-reading --help
```

PowerShell 在当前 macOS 主机不可用，因此没有直接执行 `scripts/verify.ps1`；已逐条执行脚本中的同等命令。Windows 上的脚本设置 `$ErrorActionPreference = "Stop"` 并检查每个进程退出码。

## 条件式真实验收

真实样篇尚未运行。执行它需要两个新的外部输入：本项目 `.env` 中的 `DEEPSEEK_API_KEY`，以及用户指定的中文源文件。获得后只生成一个样篇，记录 API 返回模型名、实际输入/输出 Token 和三种导出路径，然后停止等待人工审核；系统不会自动批准或启动批量。
