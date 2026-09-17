# 雅思词汇情境阅读生成器

该项目把长篇中文小说逐章转换为中文语境嵌入雅思词汇的 DOCX 和 HTML。程序按章保存、支持恢复和失败重试，并将全局学习状态写入 SQLite，同时导出 JSON 与 XLSX 快照。

## 安装

```powershell
cd C:\Users\Lenovo\Desktop\ielts-context-novel-generator
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

然后只在本机编辑被 Git 忽略的 `.env`，填写 `DEEPSEEK_API_KEY`。不要把密钥粘贴到日志、截图或提交记录中。

## 安全检查与 dry-run

```powershell
python main.py --health-check
python main.py --start 1 --end 20 --dry-run
python main.py --chapter 1 --dry-run
```

dry-run 不建立 DeepSeek 客户端，不发送请求，也不产生 API 费用。

## 样章

```powershell
python main.py --chapter 1
```

`config.yaml` 中 `batch_confirmed: false` 时，非 dry-run 每次最多处理一章。样章确认前不要修改此开关。

## 全局词库

词库是“全局雅思词库 + 间隔复现”的基础，`config.yaml` 的 `vocabulary_path` 指向它（默认 `data/vocabulary.json`）。

```powershell
# 用真实雅思词汇书（EPUB）构建词库：导入 → CEFR 标注 → 按配额补足
python work/build_full_catalogue.py "雅思词汇词根 联想记忆法：乱序版 (俞敏洪).epub" "另一个词汇书.epub"

# 纯模型生成（无书本时）
python main.py --build-vocabulary --vocabulary-target 6000
```

- 导入器支持两种版式：`单词 → 音标 → 词性. 释义` 与 `单词 → ［音标］ → 词性. 释义`（含 记/搭/例/派 块），会保留音标、常见搭配、真题例句与主题分类。
- CEFR 由模型标注（B1/B2/C1），补足部分按 `CEFR 20/60/20` 与 `动词 25% / 形容词副词 25% / 名词 20% / 固定搭配 20% / 短语动词 10%` 的缺口分桶生成，并按叙事语义域避开与书本重复。
- 构建可断点续跑：进度写入 `data/vocabulary_book.json`（书本标注缓存，按书源校验）与 `data/vocabulary_partial.json`。
- 批量处理要求词库包含 5,000～7,000 条唯一词条；单章样章不校验数量。

## 批量

```powershell
python main.py --start 1 --end 20
python main.py --resume
python main.py --retry-failed
python main.py --chapter 15
```

`config.yaml` 中 `batch_confirmed: false` 时，非 dry-run 每次最多处理一章；确认样章后再改为 `true`，并配合 `max_chapters_per_run`、`max_estimated_tokens_per_run`、`max_consecutive_failures` 使用。批量运行时：

- 默认按章串行，`concurrency` 控制并发上限（默认 3，走 `run_concurrent` 监督队列）。
- 每章处理成功后立刻落盘（`chapters/`、`html/`、`chapter_json/`、`glossary.*`、`progress.json`、`reports/usage.json`）。
- 已成功章节直接跳过；单章失败记录后继续；连续 `max_consecutive_failures` 章失败才停止整批。
- 每 `check_report_every` 章生成 `reports/check_reports/check_*.json` 检查报告。
- 每 `volume_size` 章合并一个 `volumes/第NN卷_第X至Y章.docx`（含目录字段），不会把整本书塞进一个 Word。

批量启用前必须导入并验证 5000–7000 个唯一词条的正式词库；仓库中的 `data/vocabulary_seed.json` 仅供接口和样章验证。

## 输出

```text
output/
├── chapters/
├── volumes/
├── html/
├── reports/
├── failed/
├── raw_responses/
├── glossary.xlsx
├── glossary.json
├── progress.json
└── index.html
```

每次请求的模型、时间、Token、重试状态和错误类型写入 `output/reports/usage.json`；原始模型内容写入 `output/raw_responses/`。程序不会修改 `input/` 中的原文件。

## 测试

```powershell
python -m pytest -q
python -m compileall -q src main.py
```

