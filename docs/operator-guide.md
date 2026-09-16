# IELTS Reading Studio 运维指南

## 1. 大语料导入

导入只读扫描源文件并计算 SHA-256。TXT 按 BOM、UTF-8、GB18030 顺序探测编码；DOCX 使用标题样式和标题模式；EPUB 按 spine；Markdown 使用一至三级标题。源文件不会被修改。

两千章 TXT 会先形成章节索引，再按规则合并短章或切分长章。`manifest.json` 只保存边界摘要、来源跨度和单元配置，不复制整本正文。若置信度低于 0.6，或纯文本不足两个可靠章节，单元列表为空并记录 `no_reliable_boundaries`，必须先人工检查。

网页中的边界修正写入独立的 `boundary-overrides.json`，不回写原文件。

## 2. 估算解释

每个单元基础调用为四次：来源 brief、Passage、Passage 审核、命题。一次作者返工增加“重写 + 复审”两次；一次考官返工增加一次题组局部修复。因此默认最少 4 次、最多 10 次。

中文输入 Token 以字符数除以 2.2 到除以 1.4 估算，再加各阶段固定输入输出预算。估算用于设置运行上限，不是账单承诺。未提供模型单价时只显示 Token 和请求区间。

## 3. 单元状态

- `indexed`：已索引，尚未产生模型调用。
- `author_generating`：作者正在生成 brief、Passage 或修订。
- `passage_reviewing`：程序门禁和考官正在审核 Passage。
- `author_revision_required`：只退回作者修改文章。
- `examiner_generating`：考官基于冻结 Passage 命题或修复题组。
- `validating`：程序正在检查题量、证据、答案和题型约束。
- `examiner_revision_required`：只修复失败题组，不重写 Passage。
- `completed`：规范 Package 已原子提交，可练习和导出。
- `needs_review`：达到 2+2 返工上限，需要人工处理，不继续调用。
- `failed`：单元失败，可使用失败重试。
- `paused`：不再认领新单元；在途阶段完成保存后停止。
- `cancelled`：终止状态。

任务还可能处于 `queued`、`running`、`paused`、`blocked`、`completed`、`completed_with_errors` 或 `cancelled`。401/402 会进入 `blocked` 并停止认领新单元。

## 4. 产物目录

```text
output/
├── state.db                         SQLite 权威状态
├── <corpus-slug>/manifest.json      离线语料清单
├── packages/<unit-id>.json          规范 ReadingPackage
├── stage_payloads/<unit-id>/        结构化阶段结果
├── raw_responses/<unit-id>/         响应与用量审计材料
├── reports/                         单篇与任务汇总质量报告
├── failed/                          失败代码与原因
└── exports/                         JSON / HTML / DOCX
```

写入使用同目录临时文件、`fsync` 和原子替换。SQLite 开启 WAL、外键和 busy timeout。

## 5. 中断恢复

1. 不要删除 `state.db`、`stage_payloads`、`packages` 或源文件。
2. 重新激活同一虚拟环境，运行 `ielts-reading resume <job-id>`。
3. 启动时运行中的单元会退回最近安全阶段。
4. 缓存键一致的已完成阶段直接复用；不会重复收费。
5. 只处理普通失败时，运行 `ielts-reading retry <job-id> --failed-only`。

若任务 `blocked`，先修复密钥、余额或账户问题，再恢复。不要通过直接编辑 SQLite 跳过样篇审批或状态门禁。

## 6. 备份与迁移

暂停任务并等待在途阶段落盘后，备份整个 `output/`、`config.yaml` 和原始输入文件。`.env` 应单独保管，不要放进代码仓库或共享压缩包。恢复时保持源文件可读，并让配置路径指向恢复后的 `output/state.db`。

SQLite WAL 模式下备份时应同时复制可能存在的 `state.db-wal` 和 `state.db-shm`，或在应用停止后再复制数据库。

## 7. 样篇与批量安全

真实 API 首次只生成一个样篇。人工检查来源忠实度、英文自然度、难度、题目唯一可答性、证据和中文解析，再执行 `approve-sample`。批准记录包含样篇难度和三种题型；批量配置必须与批准样篇一致。

默认批次 20、并发 2、单次最多 20 单元、最大预计 500,000 Token、最多 5 个连续失败。修改上限前先运行离线估算。

## 8. 故障排查

- `authentication_failed`：检查 `.env` 中的 `DEEPSEEK_API_KEY`，不要把值贴到日志。
- `billing_failed`：检查账户余额；系统不会自动继续队列。
- `rate_limited` / `provider_unavailable`：系统会有界重试；持续失败后保留阶段记录。
- `empty_response` / `truncated_response` / `invalid_json_response`：单元失败或进入返工，不接受不完整内容。
- `agent_schema_error`：模型 JSON 不符合 Pydantic 契约，错误会脱敏并保存。
- `needs_review`：达到返工上限；先检查 `failed/` 与 `reports/`，不要盲目重跑。

运行 `scripts/verify.ps1` 可确认本地代码、依赖和离线路径完整。真实样篇之前还应核对 DeepSeek 官方当前模型名与价格，并保持模型名由配置提供。
