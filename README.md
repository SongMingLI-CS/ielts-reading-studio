# IELTS Reading Studio

IELTS Reading Studio 是一个在本机运行的 IELTS Academic Reading 内容生成、练习与导出工具。它只读解析中文 TXT、DOCX、EPUB 或 Markdown，以可恢复的双智能体流水线生成英文 Passage 和题目，并提供 CLI、本地网页、JSON、离线 HTML 与 DOCX。

难度标签是生成目标，不是官方 Band 评分；项目不声称生成内容等同于官方 IELTS 真题。

## 安装（Windows / PowerShell）

需要 Python 3.12 或 3.13：

```powershell
git clone https://github.com/SongMingLI-CS/ielts-reading-studio.git
cd ielts-reading-studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

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

## 本地网页

```powershell
ielts-reading serve
```

默认地址是 `http://127.0.0.1:8000`，首版拒绝通过 CLI 绑定公网地址。网页提供：

- 安全上传、章节边界预览和独立修正记录；
- 生成范围、难度、三种题型、批次和并发配置；
- 样篇批准、任务暂停/继续/失败重试与状态轮询；
- 在线计时答题、本地精确评分、证据与中文解析；
- 已校验 Package 的 JSON、HTML 和 DOCX 导出。

## 导出

```powershell
ielts-reading export <job-id> --format json
ielts-reading export <job-id> --format html,docx,json --workbook-size 20
```

HTML 是不依赖网络的单文件，交卷前界面不暴露答案。DOCX 单篇可独立导出；多篇练习册每卷必须为 20–50 篇，不会把两千篇合成一个超大文件。所有格式都从同一份已校验 `ReadingPackage` JSON 渲染。

## 配置与安全上限

`config.yaml` 中可设置模型名、输出路径、并发、批次、来源切分阈值、单次最大单元数、最大预计 Token 和连续失败上限。模型名不写死在代码中。API Key 只从环境变量或项目旁的 `.env` 读取，YAML 中出现的密钥会被忽略。

认证失败或余额不足会阻断队列；限流、超时和服务端错误最多按约 1、2、4 秒加抖动重试。文章返工和题目返工分别最多两轮，超过后进入 `needs_review`，不继续收费。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

该脚本运行完整测试、语法编译、Ruff 和 CLI 帮助烟雾测试，不需要 API Key。更详细的恢复、备份和状态说明见 [运维指南](docs/operator-guide.md)。
