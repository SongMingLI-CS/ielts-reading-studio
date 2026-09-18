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

## 9. 远程部署（服务器）

服务默认只监听本机。对外提供服务时必须显式指定 host 并提供访问口令：

```bash
export IELTS_WEB_USERNAME=reader
export IELTS_WEB_PASSWORD='一个足够长的口令'
ielts-reading serve --host 0.0.0.0 --port 8000
```

- 未设置 `IELTS_WEB_USERNAME` / `IELTS_WEB_PASSWORD` 时，非本机 host 会直接拒绝启动。
- 口令只从环境变量或项目旁 `.env` 读取；YAML 中出现的口令被忽略。
- Basic 认证覆盖所有页面与静态资源，仅 `/healthz` 免认证，便于探活。
- **务必放在 HTTPS 反向代理之后。** 明文 HTTP 有两个后果：Basic 口令可被窃听；浏览器在非安全上下文（既不是 https、也不是 localhost）不提供 `crypto.randomUUID()` 等 Web API。练习页已对这类 API 做兜底，但不要把明文 HTTP 当作可接受的长期方案。
- 反代需转发 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`；`serve` 已开启 `proxy_headers`，且只信任来自本机的转发头（uvicorn 的 `forwarded_allow_ips` 默认 `127.0.0.1`）。

nginx 片段：

```nginx
server {
  listen 443 ssl;
  server_name ielts.example.com;
  ssl_certificate     /etc/letsencrypt/live/ielts.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/ielts.example.com/privkey.pem;
  client_max_body_size 250m;            # 与上传上限一致
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

其它注意事项：

- 应用必须挂在域名**根路径**。模板与脚本使用绝对路径（`/static/...`、`/practice/...`），子路径反代不生效，请改用子域名。
- 更新前端资源后浏览器可能沿用旧缓存；引用里已带 `?v=` 版本号，发布时如未更新版本号需在反代禁用 HTML 缓存或提示硬刷新。
- **当前是本机单用户设计**：练习草稿、成绩、任务队列都写在同一份 `output/state.db`，Basic 认证只拦访问者、不区分用户。多人共用一台服务器时，建议每人一份目录与端口（各自 `--config`），或在反代上按路径隔离。
- 长期运行建议用 systemd 等进程管理器托管，并定期备份 `output/`（见第 6 节）。


## 10. 更新已有部署

服务器上的项目目录就是一份 git 检出，更新只有一条命令：

```bash
~/ielts-reading-studio/scripts/deploy.sh
```

脚本会先确认没有本地改动（`output/`、`input/`、`.env`、`.env.web`、`.venv` 都在 `.gitignore` 里，git 不会碰它们），再执行 `git pull --ff-only`，随后重启 systemd 服务并打印状态。手工等价操作：

```bash
cd ~/ielts-reading-studio
git pull --ff-only
sudo systemctl restart ielts-reading-studio
```

### 首次在一台新服务器上做 git 检出

仓库是私有的，需要一个**只读部署密钥**（比放 token 安全，可随时在 GitHub 撤销）：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ielts_deploy -N "" -C "ielts-reading-studio@$(hostname -I | awk '{print $1}')"
cat ~/.ssh/ielts_deploy.pub      # 把这一行加到 GitHub 仓库 Settings → Deploy keys（勾选 read-only）
cat >> ~/.ssh/config <<'EOF'

Host github-ielts
  HostName github.com
  User git
  IdentityFile ~/.ssh/ielts_deploy
  IdentitiesOnly yes
EOF
git remote add origin git@github-ielts:<owner>/<repo>.git
git fetch origin main && git checkout -f -B main origin/main
```

`git checkout -f -B main` 只会覆盖受版本控制的文件；数据库、产物、源文件与密钥都在 `.gitignore` 中，不受影响。

### 服务器无法访问 git 主机时

在开发机上打包历史，上传后合并即可离线更新：

```bash
# 开发机
git bundle create /tmp/repo.bundle main
scp /tmp/repo.bundle <server>:/tmp/
# 服务器
cd ~/ielts-reading-studio
git fetch /tmp/repo.bundle main && git merge --ff-only FETCH_HEAD
sudo systemctl restart ielts-reading-studio
```

## 11. 设置页与导出下载

网页导航里的「设置」（`/settings`）回答两个运维问题：现在这套服务到底用的什么配置，以及出题模型还能不能用。

- **只读**：页面显示配置文件路径、出题/审校模型、API 地址、批次与并发、返工上限、切分与合并阈值、数据/产物/数据库路径和存储占用。网页不会回写 `config.yaml`，改动仍然要在服务器上编辑文件后重启服务。
- **密钥不回显**：只显示「已配置 / 未配置」以及来源（进程环境变量、项目 `.env`、服务器 `.env.web`）。页面正文和跳转参数都会经过脱敏，密钥值不会出现在 HTML 里。
- **测试模型连接**：按钮会发起一次极小的真实请求（数十 token），用来验证密钥、余额、模型名与网络；失败时把经过脱敏的异常类型和原因直接显示在页面上。这是排查「生成任务一启动就失败」最快的入口。启用前请知道它会消耗极少量额度。

「导出中心」（`/exports`）把已校验的练习导出成可下载文件：

- 生成的文件写入 `<output_dir>/exports/<批次名>/`，批次名形如 `manual-1a2b3c4d`。
- 页面提供单文件下载（`/exports/file/<相对路径>`）和整批 zip（`/exports/bundle/<批次名>`）。两条路径都只允许访问 `output/exports/` 内的文件，任何越界路径（含 `..`）一律 404。
- zip 在系统临时目录生成，下载结束后自动删除；下载中断留下的临时包会在下次备份时清理。
- 清理旧导出没有网页按钮：`rm -rf <output_dir>/exports/manual-1a2b3c4d` 即可。导出是副本，删掉不影响数据库中的题目。

## 12. 质量审阅与词汇复习（运维视角）

### 题目重复门禁

`question_duplicate_threshold`（默认 0.72）是**扣费开关**：命中的题目会进入返工循环，最多 `examiner_revision_limit` 轮，仍不合格则转 `needs_review`。如果发现某批题目大量被判重复（换模型或换题型时可能），先调低难度的严格程度再调这个值，而不是关掉它——重复题一旦进入题库，只能靠 `/review/similarity` 事后清理。审阅页阈值 `question_report_threshold`（默认 0.5）只影响展示，不影响计费。

索引按"已完成篇目集合"缓存，因此：

- 单篇重跑会重建索引一次（读全部 Package，成本随篇数线性增长）；
- 批任务只建一次索引，批内新完成的篇目实时进索引，不会自我重复。

### 抽样审阅

`review_sample_rate` / `review_sample_min` 决定每批自动抽样多少篇。抽样发生在 `run_job` 收尾，因此 CLI 的 `ielts-reading run <job-id>` 也会抽样。审阅记录存在 `review_samples` 表，删除语料库时会连同样本一起清理（外键顺序已处理）。

需要加快节奏时把 `review_sample_rate` 设成 0 可以关闭自动抽样，页面上的「再抽一批样本」仍可手动抽。

### 词汇复习

- 复习状态存在 `vocabulary_reviews` 表（盒子、到期时间、复习次数、忘记次数），与 `vocabulary_marks`（收藏/已掌握）分离：删掉收藏不会丢进度，反之亦然。
- 到期判断用 UTC；SQLite 取回的时间缺时区时按 UTC 补齐。
- 间隔是 1/2/4/8/32 天。日更场景下"今天到期"通常只有个位数，属于正常现象。
- 词汇全部来自已完成 Package 的 `passage.vocabulary`，因此**没有生成的篇目不会贡献词汇**；分类与联想都是纯本地计算，不产生任何 API 调用。

