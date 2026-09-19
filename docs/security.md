# IELTS Learning Studio 安全说明（阶段五）

本文记录**已实现的防护**、**信任边界**、**仍然存在的风险**和**公网部署要求**。
它描述当前代码的真实行为；没有做过的加固不会写在这里。

## 1. 受保护的资产

| 资产 | 位置 | 价值 |
|---|---|---|
| DeepSeek API Key | 环境变量 / `.env` / `.env.web` | 可直接产生费用 |
| 网站登录口令与会话密钥 | `IELTS_WEB_PASSWORD`、`IELTS_WEB_SESSION_SECRET` | 决定谁能进入网站 |
| 生成产物与练习/写作记录 | `output/`、SQLite `state.db` | 私人学习数据，含作文原文 |
| 源文件 | `input/` | 私人上传材料 |
| 备份归档 | `/backup/download` 输出 | 一次打包全部数据 |
| 计费额度 | Provider 调用 | Token 费用 |

## 2. 信任边界

```text
浏览器 ──(HTTP/HTTPS + 反向代理)──> uvicorn/FastAPI ──> SQLite
                                   │
                                   └──> DeepSeek/OpenAI 兼容 API（外部，付费）
worker 进程 ──> 同一个 SQLite（认领作业）──> Provider
```

- **单用户系统**：没有多用户模型、没有"他人资源"的概念。所有数据属于同一个操作者；
  访问控制是"能否进入网站"，而不是"能看哪一条记录"。见第 6 节。
- 反向代理（nginx / Caddy 等）是唯一的网络入口；应用只信任显式配置的代理地址。
- 仓库随附的生产单元把应用绑在 `127.0.0.1:8768`，由 `ielts-reading-studio-tls`（Caddy，自签内部 CA，
  按 IP 访问）在公网端口 `8766` 终止 TLS。应用不监听公网地址，因此外部无法绕过入口直连；
  代价是 `serve` 的启动闸门（第 4.8 节）不再触发，改由 `scripts/deploy.sh --check` 用同一套规则强制检查。
- Provider 返回的一切内容都是**不可信输入**：必须通过 Pydantic/Schema 校验并转义后才渲染。

## 3. 主要攻击面

1. 公开的 Web 入口（登录、表单、JSON API）。
2. 文件上传与下载路径（TXT/DOCX/EPUB 导入、导出、备份、小说产物）。
3. 付费接口（生成样篇、批量生成、重试/恢复、写作评估、连通性测试）。
4. 会改变状态的表单与 API（CSRF 面）。
5. 错误响应与日志（信息泄露面）。
6. SQLite 文件本身（会话、限速计数、作业状态）。

## 4. 已采取的控制

### 4.1 会话与 Cookie

- 服务端会话表 `web_sessions`；Cookie 只携带 256 位随机 ID，**数据库存 HMAC**
  （以 `IELTS_WEB_SESSION_SECRET` 为密钥），数据库泄露不能直接换取会话。
- Cookie 属性：`HttpOnly`、`SameSite=Lax`、`Path=/`、无 `Domain`；HTTPS 部署时附加 `Secure`
  （由 `web_force_https` 或 `web_cookie_secure` 决定）。
- 空闲过期（默认 120 分钟）与绝对过期（默认 24 小时）双重限制，过期行会被清理。
- **登录后轮换会话 ID 并重新生成 CSRF 令牌**（防会话固定）；未登录访问会先拿到匿名会话，
  这样登录动作本身也需要 CSRF 令牌。
- 退出会删除服务端会话行，旧 Cookie 立即失效。

### 4.2 CSRF

- 覆盖**所有** POST/PUT/PATCH/DELETE：中间件在路由之前执行，没有"按路由豁免"名单。
- 令牌来源：HTML 表单的隐藏字段 `csrf_token`，或 `X-CSRF-Token` 头（JSON/脚本）；
  令牌以 `secrets.compare_digest` 与会话中的值比较。
- 多部分表单（文件上传）**只接受请求头**：中间件不解析 multipart 正文，否则会破坏上传体积上限。
  浏览器侧由 `static/security.js` 把这类表单改为 fetch 提交并带上令牌（因此上传需要 JavaScript）。
- 浏览器提供的 `Origin`/`Referer` 必须同源（纵深防御）；两者都缺失时（curl 等非浏览器客户端）
  仍必须提供令牌。
- 被拒绝时返回稳定错误码：`csrf_missing_token` / `csrf_invalid_token` / `csrf_no_session` /
  `csrf_bad_origin`。令牌不写日志，也不进 URL。

### 4.3 限速（持久化）

- 计数落在 SQLite 表 `rate_limit_hits`，每个 `(bucket, window_start)` 一行，用单条 upsert 原子自增；
  多进程与重启后仍然有效，并发请求不会丢计数。
- 桶：登录失败同时按**身份**和**来源地址**计数（避免遍历用户名绕过，也不泄露账号是否存在）；
  付费/敏感接口按来源地址计数。
- 超限返回 429 与 `Retry-After`；成功登录后清零该身份与地址的失败计数。
- 客户端地址来自 TCP peer；只有当 peer 命中 `IELTS_WEB_TRUSTED_PROXIES`（IP/CIDR，默认空＝不信任）时
  才解析 `X-Forwarded-For` / `X-Forwarded-Proto`。

### 4.4 Provider 预算

- JSON/表单请求体超过 `web_max_json_body_bytes`（默认 1 MB）在解析前拒绝（413 `body_too_large`）。
- 上传在流式写入时按 `web_max_upload_bytes`（默认 250 MB）截断，超限即 413 且不留半成品。
- Prompt 超过 `max_prompt_chars`（默认 60,000 字符）→ `prompt_too_large`，**不调用 Provider**。
- 输出 Token 由服务端裁剪到 `max_output_tokens`（默认 16,000），请求参数无法抬高。
- 写作评估输入超过 `max_writing_chars`（默认 12,000 字符）→ `input_too_large`。
- 入队前用离线估算校验批次成本（`max_estimated_tokens_per_run`）；超预算的批次不占用队列。
- 重试只针对可分类为可重试的错误，次数由 `provider_max_retries`（默认 3）限制并带退避；
  SDK 自带重试被关闭。HTTP 客户端有连接/总超时（`provider_timeout_seconds` 默认 60 秒）。
- 幂等键防止重复点击产生第二次付费调用；被取消的作业不会被 worker 重新认领。

### 4.5 安全响应头与缓存

- `Content-Security-Policy`：`default-src 'self'`、`script-src 'self' 'nonce-<每响应随机>'`、
  `frame-ancestors 'none'`、`object-src 'none'`、`base-uri 'self'`、`form-action 'self'`；
  **不使用 `unsafe-eval`**，注入的 `<script>` 会被拦截。模板中 4 处内联脚本都带同一 nonce。
- 其余：`X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、
  `Permissions-Policy`（关闭相机/麦克风/定位/支付等）、`Cross-Origin-Opener-Policy: same-origin`、
  `X-Frame-Options: DENY`、`X-Permitted-Cross-Domain-Policies: none`。
- 除 `/static/` 外的所有响应都带 `Cache-Control: no-store` 与 `Pragma: no-cache`；
  静态资源按原样缓存。
- `Strict-Transport-Security` 只在请求被判定为 HTTPS 时发送，避免误伤本地 HTTP 开发。

### 4.6 上传、文件名与路径

- 扩展名白名单 + 容器格式**魔术字节**校验（DOCX/EPUB 必须是 ZIP 容器），不信任浏览器 MIME 与文件名。
- 服务端生成文件名/uuid 路径；展示用的名字只取 basename 并清理控制字符。
- 上传先写临时文件，失败即删除，不留半成品。
- 下载（导出、小说产物、备份）都做 `resolve()` 后前缀校验；指向目录外的符号链接同样 404。

### 4.7 错误与日志

- 未处理异常对外只返回 `{"code": "internal_error", "detail": "服务器内部错误，请稍后重试"}`，
  不含调用栈、路径、SQL；完整异常只进服务端日志。
- 日志中的 `Authorization`、`Cookie`、`Set-Cookie`、`X-CSRF-Token`、`api_key`、
  `IELTS_WEB_PASSWORD`、`IELTS_WEB_SESSION_SECRET`、数据库 URL 凭据统一脱敏；
  不记录作文/小说正文与完整 Prompt。
- 会话令牌与 CSRF 令牌不写日志。

### 4.8 生产配置闸门

`ielts-reading serve --host 0.0.0.0` 在绑定端口前检查以下条件，任一不满足即**拒绝启动**并打印中文原因：

- 必须设置 `IELTS_WEB_USERNAME` 与 `IELTS_WEB_PASSWORD`，口令至少 12 字符且不在弱口令列表；
- 口令长度下限可用 `IELTS_WEB_PASSWORD_MIN_LENGTH` 显式放宽（最低 8）：这是一处**可见的**配置决定，
  只影响这一项检查，弱口令列表、HTTPS 声明、会话密钥与可信代理四项仍然强制（见第 5 节第 11 条）；
- 必须声明 HTTPS：`IELTS_WEB_FORCE_HTTPS=1`；
- 必须设置 `IELTS_WEB_TRUSTED_PROXIES`（用于判断真实协议与客户端地址）；
- 必须设置 `IELTS_WEB_SESSION_SECRET`（至少 32 字符，且不是示例值）。

`IELTS_WEB_FORCE_HTTPS=1` 时，非 HTTPS 请求（`/healthz` 除外）返回 403 `https_required`。

闸门只在绑定**非回环**地址时求值。随附单元把应用绑在回环（TLS 入口之后），所以同一套规则由
`scripts/deploy.sh --check` 在部署前强制执行，并在重启后用「带 `X-Forwarded-Proto: https` 请求 `/`」
验证最后一跳：否则一个缺失的 `IELTS_WEB_SESSION_SECRET` 会静默退化成开发密钥，而
`/healthz`（公开路径）仍会返回 200，让人误判部署成功。

## 5. 明确未解决的剩余风险

1. **CSRF 依赖 JavaScript**：多部分上传表单由 `static/security.js` 改为 fetch 提交；
   禁用 JS 的浏览器无法上传文件（普通表单仍可用）。这是为了不破坏流式体积上限的取舍。
2. **`style-src` 仍允许 `unsafe-inline`**：4 个模板使用 `style=""` 属性。脚本侧没有 `unsafe-inline`，
   因此注入 `<script>` 仍被拦截；但注入样式属性（界面欺骗类）不在防护范围内。
3. **单用户模型**：没有多租户隔离。任何持有凭据的人都能看到全部数据；
   若将来多人使用，需要引入真正的用户表、对象级授权与逐用户限速。
4. **HTTP Basic 每次请求都携带口令**：靠 HTTPS 保护传输，无法做到更细的登录审计与"记住我"。
5. **没有双因素认证**：口令强度是唯一屏障。
6. **SQLite 单写者**：限速与队列共用一个数据库文件；极端写入压力下表现为忙等待而非隔离。
7. **没有 WAF/入侵检测**：只做基本限速与输入校验，不识别复杂攻击模式。
8. **CSRF 令牌不按请求轮换**：随会话有效期（登录/轮换时更新）。
9. **备份归档未加密**：`/backup/download` 的 zip 是明文，下载后请自行加密存放。
10. **未做依赖漏洞扫描与外部渗透测试**（`pip-audit`、第三方评估均未执行）。
11. **本次部署的口令长度低于默认下限**：生产 `.env.web` 把 `IELTS_WEB_PASSWORD_MIN_LENGTH` 设为 10，
    沿用学生已经在用的口令而不是强制全体重发。补偿控制是登录限速（默认每 5 分钟 10 次，按身份与来源
    地址分别计数，失败计数落在持久表里）与「唯一入口 + 只监听回环」的边界。口令一旦轮换，
    应把该变量改回 12 或直接删除。

## 6. 对象级权限现状（重要）

当前产品是**单用户**系统：

- 认证之后所有对象（corpus、unit、job、snapshot、novel 产物、上传文件）都属于同一个操作者，
  不存在"越权访问他人对象"的多用户场景。
- 因此本阶段**没有**引入 RBAC 或按用户过滤查询，取而代之的是：
  - 未认证请求对所有对象 URL 返回统一响应，不区分"存在/不存在"，避免帮助枚举；
  - 对象查询都带唯一用户这一隐含所有者条件，不做"先查后判"的松散判断；
  - 取消/暂停/重试/删除等敏感操作重新读取最新状态（避免 TOCTOU）；
  - 文件下载限制在配置目录内，符号链接逃逸也被拒绝。
- 公网部署时请确认边界：只经反向代理暴露、启用 HTTPS、使用强口令，并把
  `IELTS_WEB_TRUSTED_PROXIES` 精确写成代理地址（不要用 `0.0.0.0/0`）。

## 7. 生产部署要求（清单）

- [ ] 反向代理终止 TLS，并转发 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`。
- [ ] 应用只监听回环地址（随附单元即 `127.0.0.1:8768`），公网端口由 TLS 入口独占；
      明文请求得不到 HTTP 响应，也没有可绕过的第二个监听端口。
- [ ] 自签入口用内部 CA 签发 SAN=IP 证书；换成已备案域名后应改为受信任证书。
- [ ] 部署前 `scripts/deploy.sh --check` 通过（它复刻了 `serve` 的启动闸门）。
- [ ] `IELTS_WEB_FORCE_HTTPS=1`；`IELTS_WEB_TRUSTED_PROXIES` 只写代理地址/CIDR。
- [ ] `IELTS_WEB_SESSION_SECRET` ≥ 32 字符随机值（例如 `openssl rand -hex 32`），单独保管。
- [ ] `IELTS_WEB_USERNAME` / `IELTS_WEB_PASSWORD` 使用强口令（≥12 字符，无示例值）。
- [ ] 运行 `python -m app.cli migrate`（`0003` 创建会话与限速表）后再启动 web 与 worker。
- [ ] `/healthz` 只返回存活信息，不要把内部端点直接暴露到公网。
- [ ] 定期查看 `journalctl -u ielts-reading-studio`，确认没有持续的 403/429 峰值。
- [ ] 限速与预算保持默认值；确需放宽时先在本地验证行为。

