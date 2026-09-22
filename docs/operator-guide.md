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

网页提交的生成任务由独立 worker 执行，不再依赖网页进程存活：网页只负责入队，worker 认领后持有租约并定时续租。

1. 不要删除 `state.db`、`stage_payloads`、`packages` 或源文件。
2. 确认 worker 在运行：`systemctl status ielts-reading-studio-worker`、`journalctl -u ielts-reading-studio-worker -n 50`。
3. 手工驱动一次：`ielts-reading worker --once`（处理当前队列中的作业后退出）。
4. 重新激活同一虚拟环境，运行 `ielts-reading resume <job-id>`（CLI 会同步执行一遍，适合单机排查）。
5. 启动时运行中的单元会退回最近安全阶段；worker 崩溃后租约过期，其它 worker 会自动重新认领（任务页显示「中断待恢复」）。
6. 缓存键一致的已完成阶段直接复用；不会重复收费。
7. 只处理普通失败时，运行 `ielts-reading retry <job-id> --failed-only`。

队列语义（`jobs.status`）：`queued` → `running` → `completed` / `completed_with_errors` / `failed` / `cancelled`，`paused` 与 `blocked` 是可逆的等待状态。作业行另外记录 `worker_id`、`lease_expires_at`、`heartbeat_at`、`attempts`、`error_code`，用于判断「等待 worker / worker 处理中 / 中断待恢复」。同一个作业连续 3 次因租约过期而中断会被标记为 `failed`（`error_code=worker_lease_expired`），避免无限重试。

若任务 `blocked`，先修复密钥、余额或账户问题，再恢复。不要通过直接编辑 SQLite 跳过样篇审批或状态门禁。

## 6. 备份与迁移

暂停任务并等待在途阶段落盘后，备份整个 `output/`、`config.yaml` 和原始输入文件。`.env` 应单独保管，不要放进代码仓库或共享压缩包。恢复时保持源文件可读，并让配置路径指向恢复后的 `output/state.db`。

SQLite WAL 模式下备份时应同时复制可能存在的 `state.db-wal` 和 `state.db-shm`，或在应用停止后再复制数据库。

### Schema 版本（Alembic）

数据库 schema 由 Alembic 管理，迁移脚本在 `migrations/versions/`，当前基线为 `0001`。应用启动（`serve`、CLI、网页）时自动迁移；也可以手动执行：

```bash
ielts-reading migrate --check     # 只读检查，落后时退出码 2
ielts-reading migrate             # 升级到最新版本
```

- 旧库（有应用表但没有 `alembic_version`）会被标记为 `0001`，不重建表、不删数据。
- 迁移失败时进程以非零码退出，服务不会在半迁移状态下启动。先修复原因（磁盘、权限、锁），再重试。
- 数据库记录的版本比代码新时会被拒绝，避免旧代码把新库当作旧 schema 使用。

**回滚**（仅在明确需要退回旧版本代码时）：

```bash
systemctl --user stop ielts-reading-studio   # 或 sudo systemctl stop ielts-reading-studio
cp output/state.db output/state.db.before-rollback
IELTS_DATABASE_URL="sqlite+pysqlite:///$PWD/output/state.db" .venv/bin/alembic downgrade -1
git checkout <上一个发布 commit>             # 或 git revert
sudo systemctl start ielts-reading-studio
curl -fsS http://127.0.0.1:8766/healthz
```

回滚前必须保留 `state.db.before-rollback`：降级迁移可能丢弃新版本写入的列或表。如果某个迁移没有写 `downgrade()`，就恢复快照而不是降级。

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

运行 `scripts/verify.sh`（Linux / macOS，服务器上用这个）或 `scripts/verify.ps1`（Windows）可确认本地代码、依赖和离线路径完整：两个测试套件、`app/` 与组件源码的字节编译、四处 Ruff 检查与 CLI 帮助烟雾测试。`verify.sh` 默认使用项目里的 `.venv/bin/python`（systemd 启动服务的同一个解释器），可用 `PYTHON=` 覆盖。真实样篇之前还应核对 DeepSeek 官方当前模型名与价格，并保持模型名由配置提供。

## 9. 远程部署（服务器）

### 9.1 本服务器的实际拓扑（按 IP + 自签 HTTPS）

生产机（192.144.160.196）不直接暴露 uvicorn，前面有一层自签 HTTPS 入口：

```text
浏览器 ──https(自签, SAN=IP)──> Caddy 0.0.0.0:8766 ──http──> uvicorn 127.0.0.1:8768 ──> SQLite
                                        └── 转发 Host / X-Forwarded-For / X-Forwarded-Proto: https
```

这样安排的原因（都是本机实测结论）：

- **80 端口被别的服务占用**（容器），无法用 ACME 的 http-01 校验；
- **443 端口留给同机的另一个项目**（Novel Agent 的 Caddy 配置），不抢用；
- **未备案域名在大陆机房会被按 SNI 拦截**，而**浏览器访问 IP 时按 RFC 不发 SNI**：
  实测「无 SNI」的握手会以 `tlsv1 alert internal error` 失败，因此 Caddy 配置里用
  `default_sni <站点主机名>` 兜住这种情况（设成公网 IP 后，无 SNI 握手与带 SNI 一样拿到
  SAN=IP 的内部证书）。浏览器只会提示「证书不受信任」，不会有域名不匹配；
- 8766 早已在云安全组放行，换成 https 不需要改安全组。

安装与更新用一条命令（写入 `.env.web` 缺失项，并安装/更新 web、worker、HTTPS 入口三个单元）：

```bash
scripts/server-setup.sh          # 只报告：缺哪些变量、哪些单元没装、端口是否一致
scripts/server-setup.sh --apply  # 落地；会话密钥在本机生成，且从不打印
```

用户访问 `https://192.144.160.196:8766`，首次需要点一次「继续前往」（自签证书）。
应用自身**只监听 127.0.0.1**：明文请求打到 8766 会因是 TLS 端口而得不到 HTTP 响应，
8768 又不对外监听，外部无法绕过入口。

以后有了**已备案域名**：把 `IELTS_TLS_SITE` 改成 `https://你的域名`、去掉 `deploy/Caddyfile`
里的 `tls internal`，并让 80 端口或 DNS 能完成 ACME 校验，即可换成受信任证书。

### 9.2 配置闸门（不使用随附单元时）

服务默认只监听本机。对外提供服务时必须显式指定 host，并满足[安全说明](security.md)第 4.8 节的配置闸门；
任一条件不满足会**拒绝启动**并打印具体原因：

```bash
export IELTS_WEB_USERNAME=reader
export IELTS_WEB_PASSWORD='一个至少 12 字符的强口令'
export IELTS_WEB_SESSION_SECRET="$(openssl rand -hex 32)"
export IELTS_WEB_FORCE_HTTPS=1
export IELTS_WEB_TRUSTED_PROXIES=127.0.0.1
ielts-reading serve --host 0.0.0.0 --port 8766
```

生产环境建议把这些写进 systemd 的 `EnvironmentFile`（`.env.web`），不要写在明文 shell 历史里。

- 未设置 `IELTS_WEB_USERNAME` / `IELTS_WEB_PASSWORD` 时，非本机 host 会直接拒绝启动；
  口令少于 12 字符或属于示例弱口令同样会被拒绝。
- **必须同时设置** `IELTS_WEB_SESSION_SECRET`（≥32 字符）与 `IELTS_WEB_FORCE_HTTPS=1`
  以及 `IELTS_WEB_TRUSTED_PROXIES`（代理地址/CIDR）。缺少任一项都会拒绝启动。
- 口令与会话密钥只从环境变量或项目旁 `.env` 读取；YAML 中出现的口令被忽略。
- Basic 认证覆盖所有页面与静态资源，仅 `/healthz` 免认证，便于探活；登录后会建立服务端会话，
  所有状态变更请求还需要 CSRF 令牌（见[安全说明](security.md)第 4.2 节）。
- **务必放在 HTTPS 反向代理之后。** 明文 HTTP 有两个后果：Basic 口令可被窃听；浏览器在非安全上下文（既不是 https、也不是 localhost）不提供 `crypto.randomUUID()` 等 Web API。练习页已对这类 API 做兜底，但不要把明文 HTTP 当作可接受的长期方案。
- 反代需转发 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`；`serve` 已开启 `proxy_headers`，且只信任来自本机的转发头（uvicorn 的 `forwarded_allow_ips` 默认 `127.0.0.1`）。
- 闸门只在绑定**非回环**地址时触发。随附的 web 单元绑 `127.0.0.1`（回环在 TLS 入口之后），
  因此同样的规则由 `scripts/deploy.sh --check` 强制执行：缺少会话密钥、未声明 HTTPS 或未配置
  可信代理时**直接拒绝部署**，避免静默退化成开发密钥。

### 9.3 nginx 片段（域名 + 受信任证书）

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

服务器上的项目目录就是一份 git 检出。日常更新用一条命令，它会一路做到「服务真的能响应」：

```bash
~/ielts-reading-studio/scripts/deploy.sh
```

它按顺序执行：

1. **部署前检查**（只读）：工作树是否干净、当前 commit、`.venv` 是否存在、`config.yaml` 与 `.env.web` 是否可读、
   **公网闸门**（凭据、会话密钥、HTTPS 声明、可信代理——与 `serve` 启动时的规则完全一致）、`input/`/`output/`
   是否可读写、磁盘空间、当前 schema 版本与待迁移状态。
2. **一致性快照**：用 SQLite online backup 把 `state.db` 写到 `backups/deploy-<UTC时间>.db`，只保留最近 10 份（`IELTS_KEEP_SNAPSHOTS` 可调）。快照失败会直接中止部署。
3. `git pull --ff-only`。
4. `uv sync --frozen` 按 `uv.lock` 同步依赖（存在 uv 时优先；没有 uv 或 `uv.lock` 时回退到 `pip install -e`，
   版本不做精确锁定）。**依赖必须跟着代码走**：新版本可能新增运行时包（例如 `python-multipart`、`openpyxl`）
   或包路径（`ielts_novel` 来自仓库内的 `components/context-novel/src`），只拉代码不装依赖会让服务起不来。
   服务器上不想要测试工具时用 `IELTS_EXTRAS=`（显式空值）只装运行时依赖；不设置该变量则默认安装 `dev` extra。
5. `ielts-reading migrate` 执行数据库迁移，失败即中止。
6. 离线验证：`python -m pytest -q`（无网络、无 API Key）。
7. `sudo systemctl restart ielts-reading-studio`，然后**真正请求**两件事：
   (a) 应用自身的 `http://127.0.0.1:8768/healthz`（最多 20 次 × 1 秒；端口从 systemd 单元里读取，
   可用 `IELTS_HEALTH_URL` 覆盖）；(b) 带 `X-Forwarded-Proto: https` 请求 `/`，确认它不是 403
   `https_required`。`systemctl is-active` 只能说明进程活着，不能说明网站可用；而 `/healthz` 属于公开
   路径，在「全站 403」时仍然是 200，所以 (b) 不能省——它验证的正是 TLS 入口到应用的最后一跳。

任何一步失败都会走**回滚**：停止服务 → 把代码切回部署前的 commit（`git switch --detach`，不使用破坏性重置）→ 如果已经执行过迁移就恢复快照并删除 `-wal`/`-shm` → 重启旧版本并再次做健康检查。回滚后仍不健康时，脚本打印脱敏后的 `journalctl` 并给出明确的手工处理指引，服务保持运行而不是停在半升级状态。

检查与演练（都不修改任何状态）：

```bash
scripts/deploy.sh --check      # 只做第 1 步（含公网闸门）；不可部署时退出码 1，可部署时 0
scripts/deploy.sh --dry-run    # 打印第 2–7 步会执行的命令，一条都不执行
scripts/deploy.sh --no-pull    # 已经用 git bundle 手工更新过代码时使用
scripts/deploy.sh --skip-tests # 跳过第 6 步（不推荐）
```

`--check` 可在 systemd timer 或监控里定期运行：它不会创建数据库文件，也不做任何写操作。

手工等价操作（顺序不能颠倒：先拉代码，再装依赖，最后重启）：

```bash
cd ~/ielts-reading-studio
python -m app.cli snapshot --target backups/manual.db   # 先留一份可回滚的数据
git pull --ff-only
uv sync --frozen --extra dev
python -m app.cli migrate
python -m pytest -q
sudo systemctl restart ielts-reading-studio
curl -fsS http://127.0.0.1:8766/healthz
```

更新完成后也可以只跑一次自检：

```bash
cd ~/ielts-reading-studio && scripts/verify.sh
```

### systemd 加固

`deploy/ielts-reading-studio.service` 在重启策略之外还开了 `NoNewPrivileges`、`PrivateTmp`、`PrivateDevices`、`ProtectSystem=strict`、`ProtectHome=read-only`、`RestrictAddressFamilies`、`MemoryDenyWriteExecute` 等开关，并用 `ReadWritePaths=/home/ubuntu/ielts-reading-studio` 保留应用真正需要写入的目录（`output/`、`input/`、`backups/`）。日志统一进 journal（`SyslogIdentifier=ielts-web`），没有需要轮转的日志文件。

修改 unit 后执行：

```bash
sudo cp deploy/ielts-reading-studio.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart ielts-reading-studio
systemctl show ielts-reading-studio -p ProtectSystem -p NoNewPrivileges
```

如果 `ReadWritePaths` 覆盖不到你的数据目录（例如 `config.yaml` 把 `output_dir` 指到别处），服务会因为只读文件系统而启动失败——按路径把目录加进 `ReadWritePaths`，不要为此关掉 `ProtectSystem`。

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

### 知识点汇总的数据依赖

`/knowledge` 是只读视图，删产物/删语料库之后它会自动缩小，不需要迁移：

- 阅读侧读 `output/packages/*.json`，只统计 `completed` 的单元；某篇被返工或删除时词条随之消失。
- 小说侧读当前书的 `output/context-novel/books/<书 id>/glossary.json` 与 `chapter_json/`（书由 `input/context-novel/books.json` 索引）。**这两个文件缺失时页面照常工作**，只显示顶部一行提示；小说未生成前不必做任何配置。页面上换一本看（`?book=`）或换生成目标（`/novel/select`）就会换成那本书的文件。
- 小说术语库的解析结果按文件指纹（mtime + 大小 + 章节数）在内存里缓存，最多 8 份；
  重新生成章节后指纹变化会自动重读，不需要重启服务。
- 术语库里的中文释义质量取决于小说生成时的模型输出（早期数据可能是"积"这类过短的释义），
  页面把搭配与例句放在同样显眼的位置就是为了兜住这一点。要改善释义质量，
  该批章节重新生成即可；重新生成会重建 glossary，页面下次请求即生效。

### 词汇复习

- 复习状态存在 `vocabulary_reviews` 表（盒子、到期时间、复习次数、忘记次数），与 `vocabulary_marks`（收藏/已掌握）分离：删掉收藏不会丢进度，反之亦然。
- 到期判断用 UTC；SQLite 取回的时间缺时区时按 UTC 补齐。
- 间隔是 1/2/4/8/32 天。日更场景下"今天到期"通常只有个位数，属于正常现象。
- 词汇全部来自已完成 Package 的 `passage.vocabulary`，因此**没有生成的篇目不会贡献词汇**；分类与联想都是纯本地计算，不产生任何 API 调用。

## 13. 安全运维（阶段五新增）

完整威胁模型见 [安全说明](security.md)。日常运维需要知道的几点：

### 升级到含安全加固的版本

```bash
cd ~/ielts-reading-studio
scripts/deploy.sh --check          # 只读预检
scripts/deploy.sh                  # 快照 → 拉取 → 依赖 → 迁移 0003 → 测试 → 重启 web+worker → /healthz
```

`0003` 迁移创建两张表：`web_sessions`（服务端会话）与 `rate_limit_hits`（限速计数）。
过期会话在请求路径上按批清理；限速窗口在写入时顺带清理过期行，两者都不需要额外的定时任务。

### 部署前的服务器前置步骤（推荐先跑一次）

`scripts/server-setup.sh` 检查并（加 `--apply` 时）补齐公网部署必需的前置项，**默认 dry-run，不修改任何文件**：

```bash
cd ~/ielts-reading-studio
git pull --ff-only                 # 先拿到新代码（含迁移 0003 与新的 deploy.sh）
scripts/server-setup.sh            # 只报告：.env.web 缺哪些键、worker 服务是否已安装
scripts/server-setup.sh --apply    # 写入缺失键（会先备份 .env.web 并 chmod 600）、安装 worker 服务
scripts/deploy.sh                  # 正式部署（快照 → 拉取 → 依赖 → 迁移 → 测试 → 重启 → /healthz）
```

- 脚本**从不打印密钥**：`IELTS_WEB_SESSION_SECRET` 在服务器本机用 `openssl rand -hex 32` 生成后直接写入文件。
- `IELTS_WEB_USERNAME` / `IELTS_WEB_PASSWORD` 需要你手工填写，脚本只提示不生成。
- 未安装 worker 服务时页面不会报错，但生成任务会一直停在「等待 worker」——脚本会明确提醒这一点。
- `--no-worker` 可以只处理环境变量；`IELTS_TRUSTED_PROXIES_DEFAULT` 可改默认的 `127.0.0.1`。

| 变量 | 开发默认 | 生产建议 | 说明 |
|---|---|---|---|
| `IELTS_WEB_SESSION_SECRET` | 空（用固定占位值） | `openssl rand -hex 32` | 会话 ID 的 HMAC 密钥；**公网必填**，更换会让所有人重新登录 |
| `IELTS_WEB_FORCE_HTTPS` | 空 | `1` | 声明 HTTPS；同时让 Cookie 带 `Secure`、启用 HSTS，并拒绝明文请求（`/healthz` 除外） |
| `IELTS_WEB_TRUSTED_PROXIES` | 空（不信任任何转发头） | 代理地址/CIDR | 决定是否解析 `X-Forwarded-For` / `X-Forwarded-Proto` |
| `IELTS_WEB_COOKIE_SECURE` | 由上面推导 | 一般不用设 | 仅特殊代理拓扑下强制 `Secure` |
| `IELTS_WEB_PASSWORD_MIN_LENGTH` | `12` | 一般不设 | 公网闸门要求的口令长度下限（最低 8）。调低是**有意的风险取舍**：只放宽这一项，弱口令列表与其余闸门不变；口令轮换后应改回 12 |

限速与预算阈值可在 `config.yaml` 调整（`web_login_rate_limit`、`web_task_rate_limit`、
`web_sensitive_rate_limit`、`web_max_json_body_bytes`、`web_max_upload_bytes`、`max_prompt_chars`、
`max_output_tokens`、`provider_timeout_seconds`、`provider_max_retries`、`max_writing_chars`），
改完需要重启 `ielts-reading-studio`。

### 常见现象

- **任务一直"等待 worker"**：worker 服务没在跑（`systemctl status ielts-reading-studio-worker`），
  或提交被限速拒绝（journal 里会有 `rate_limited_*`）。
- **表单提交 403 且提示 CSRF**：页面在会话过期后停留太久，或用了旧标签页；刷新即可。
  程序化调用先 `GET /api/csrf-token` 并保留返回的 `ielts_session` Cookie。
- **多部分上传在禁用 JavaScript 的浏览器失败**：设计取舍，见安全说明第 5 节第 1 条。
- **登录返回 429**：触发登录限速（默认 10 次 / 5 分钟，按身份与来源地址同时计数）。
- **`serve` 拒绝启动**：输出逐条列出缺少的配置，按提示补齐 `IELTS_WEB_*` 变量即可。

