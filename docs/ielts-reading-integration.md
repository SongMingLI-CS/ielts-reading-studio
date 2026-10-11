# IELTS 阅读模块：运行与维护

最后复核：2026-10-11。当前定位为**单篇 IELTS 阅读训练的日常使用候选稳定版**。本轮实测、截图、数据安全与冻结结论统一见根目录 `IELTS_Reading_Final_Acceptance.md`。历史报告保留作审计，不再作为当前验收结果。

公开仓库仅发布代码、测试与文档；`outputs/`下的截图、隔离数据、快照和回滚证据保留在验收机器，本地证据路径不是线上运行依赖。

## 1. 功能与边界

文章浏览、Study/Exam、草稿、题型分组、题号导航、复查、正文高亮、字号调整、成绩、中文解析、段落证据和错题复习已接入正式业务。桌面左右独立滚动，760px 及以下切换文章/题目。

正式题型为 Matching Headings、Matching Information、Multiple Choice、True/False/Not Given、Yes/No/Not Given、Sentence Completion、Summary Completion、Short Answer。多选题按既有题目选项和指令处理，不改变模型。词数提示是辅助提示，最终匹配由现有评分器决定。

不支持三篇/40题完整模考、完整考试级统一交卷、Matching Features、Table/Note Completion、离线持久草稿、无人请求时后台自动结算。AI 题目内容没有经过系统性的人工质量审计。不要称为官方完整模拟考试。

## 2. 正式入口与路由

- `GET /practice`：文章浏览、搜索与状态筛选。
- `GET /practice/{unit_id}`：继续同模式草稿；`?attempt=ID` 指定记录。已交卷记录跳转成绩。
- `?mode=study|exam`：选择模式；`?fresh=1&mode=...`：新练习，不覆盖旧草稿或历史成绩。
- `POST /practice/{unit_id}/start`：建立稳定 attempt ID；学习直接开始，考试明确确认后开始服务器计时。重复 start 不重置考试截止时间。
- `POST /practice/{unit_id}/save`：保存答案、用时及 UI 状态，携带 revision。
- `GET /practice/{unit_id}/state/{attempt_id}`：同步状态，`Cache-Control: no-store`；到期时可能完成交卷，不能将其当纯读接口去批量探测正式练习。
- `POST /practice/{unit_id}/submit`：正式评分与交卷，JSON 返回成绩入口，传统表单返回 303。
- `GET /practice/{unit_id}/result/{attempt_id}`：已交卷成绩及中文解析；未交卷返回 409。
- `GET /practice/{unit_id}/analysis?attempt=ID`：全篇解析与词汇；同篇活跃考试期间返回 409。历史已交卷成绩仍按既有规则可查看，不属于封闭考试系统。
- `GET /practice/history`：学习记录。
- `GET /practice/review?tab=mistakes`：错题复习；另有 words/due 标签。`/practice/mistakes` 保留既有独立入口。
- `GET /practice/mistakes/print`：错题打印。

## 3. 文件结构

- `app/web/routes_practice.py`：路由、状态转换、正式评分 `_score_submission`/`_matches`、复习与解析上下文。
- `app/web/schemas.py`：PracticeStart、PracticeSubmission、ReadingUIState、响应结构。
- `app/storage/repositories.py`：`mutate_practice_attempt` 原子状态变更；`app/storage/database.py`、`app/storage/migrations.py` 与 `migrations/` 管理存储结构。
- `app/models.py`：ReadingPackage/QuestionType；`app/storage/artifacts.py` 保存 package。解析从正式 package 读取。
- `templates/practice/index.html`：书架；`session.html`：作答工作区；`result.html`：成绩双栏；`analysis.html`：全篇解析。
- `templates/practice/review/_mistakes.html`：复习内容；`review/index.html`/`mistakes.html` 等沿用全站外壳。
- `static/practice.js?v=7`：草稿、导航、滚动、高亮、计时、提交与冲突。
- `static/practice-result.js?v=2`：解析题号同步、错题筛选、证据、解析入口错误反馈。
- `static/ielts-workspace.css?v=2`：工作区样式；书架/复习使用现有全站样式。
- `static/request.js?v=1`：15秒请求超时、CSRF、HTTP/网络错误封装；`static/security.js?v=1` 和 `app/web/security_gate.py`：现有安全规则。

## 4. Study Mode

不强制限时；活动用时沿用客户端可见页面计时并随草稿保存。正文高亮范围、字号（18–22px）、复查题号、当前题、移动面板和两栏锚点/偏移保存在 payload.ui。学习用时不是可靠的考试计时来源。

左侧文章滚动只记录阅读位置，不更换当前题。点击题号或聚焦控件更新当前题；右侧滚动在当前输入题仍可见时尊重焦点，离开可见范围后跟随题目。切换题型定位题组标题和完整说明；点击具体题号则定位题目。

## 5. Exam Mode 与截止时间

启动弹窗说明这是单篇训练，UI 可选20/30/60分钟；接口验证分钟范围1–60，本轮1分钟仅为隔离测试。服务器写入 `started_at`、`deadline`、`duration_seconds`。浏览器以服务器 epoch 加 performance.now 的差值展示倒计时，刷新或重新 start 不重置 deadline。

到期后禁用作答并请求同步；`state/save/submit` 的事务都检查服务器时间。超时按最后已获服务器确认的答案评分，拒绝迟到输入，保存 timed_out 与终态。离开页面仍计时；没有后台 worker 自动结算，完全无请求时数据库可暂保持 in_progress，后续请求再结算。

考试提交前没有本次正确答案、解析或高亮辅助入口。复查可用，交卷后统一反馈。保护范围是既有单用户学习系统：不保证防止用户另开历史结果、查看本地 package 或其他已授权资源。

## 6. 草稿与刷新恢复

`practice_attempts` 是唯一持久来源，无第二套 localStorage 草稿。字段沿用 `payload.answers`、elapsed_seconds；revision/mode/ui/考试时间均在既有 JSON payload 中，没有为工作区增表。

输入约500ms防抖、滚动约800ms、每10秒保存脏状态或同步；手动立即保存。请求串行化，成功响应确认 revision。页面只保留未保存输入在内存中；网络失败明确提示未保存，可重试。刷新恢复服务器最后确认版本，不能保证恢复失败请求后的本地输入。页面隐藏时尝试保存，beforeunload 对未保存状态警告，但浏览器关闭/进程终止不能保证最后请求送达。

## 7. 提交、评分与复习

提交弹窗显示遗漏与复查数量。客户端先排空保存队列，再以已确认 revision 提交；界面阻止重复操作。服务器 `BEGIN IMMEDIATE` 在读取前取得写保留锁，把 revision/deadline/终态作为一个事务处理。发生异常回滚事务。

`in_progress → submitted` 写入 score/total/submitted_at；已提交 save 返回409，重复 submit 返回原结果且不覆写答案。评分复用 `_score_submission` 和 `_matches`，结果页再次使用正式 package 与已保存答案构建解析。不要自行修改正确答案或评分器。

错题由已提交记录通过 `mistake_items` 派生，按现有累计规则呈现，不维护第二份错题状态。解析显示中文说明、原文句子及段落定位；不是自动逐句精确跳转。重做建立新的 attempt，不删除旧成绩、旧草稿或复习来源。

## 8. 多标签页冲突

保存/交卷需匹配当前 revision。旧标签返回409，保留页面输入，禁用交卷，显示重新加载服务器草稿；同步发现较新 revision 也进入阻塞状态。不自动合并答案。

重新加载按钮在 dirty 时调用原生确认框：代码中取消分支不导航、不清除输入；确认分支才清 dirty 并加载指定 attempt。**本轮浏览器工具只能可靠验证弹窗实际打开，取消/确认后的真实浏览器结果仍需人工补验**。不要把源码审查等同于该路径的浏览器通过。

## 9. 安全与异常

所有 unsafe 请求沿用 CSRF 与会话安全中间件；同源 fetch 使用现有 cookie，request.js 从 meta 携带 token。不要关闭 CSRF 来排查保存错误。公开部署沿用 HTTP Basic/Auth、HTTPS、来源校验和限速要求，详见 `security.md`、`operator-guide.md`。

保存断网保留内存输入、显示未保存；交卷响应丢失后请求 state，同步服务器已交卷结果。全篇解析先探测受保护路由，409显示原因、重试与返回限时练习；成功再导航。两次GET是权限反馈探测和页面导航，不是两次交卷。

## 10. 测试方法

全套：

```sh
.venv/bin/python -m pytest -q
```

针对阅读/安全：

```sh
.venv/bin/python -m pytest -q tests/web/test_practice_workspace.py tests/web/test_practice.py tests/web/test_auth.py tests/web/test_ui_contracts.py
```

现有 pytest fixture 为每个测试建立 tmp_path AppConfig/SQLite。安全拒绝测试使用无 CSRF 头客户端；共享正常客户端 bootstrap token。不要把 config.yaml 正式路径传给测试 fixture。

本轮正式代码隔离服务：

```sh
PYTHONPATH=. .venv/bin/python outputs/ielts-final-acceptance/serve_isolated.py
```

地址8894，数据仅在 `outputs/ielts-final-acceptance/runtime/`；代理8895仅用于故障注入。`tests/browser/practice_p2_regression.mjs` 导出 sessionRegression/resultRegression，须用受支持的 CUA Tab 与实际当前标签视口调用；Study/Exam各从未作答12题草稿开始，Exam需先启动。不要给已答题夹具重复运行会切换复查状态的脚本。视口操作只影响当前标签，必须检查 innerWidth，避免伪手机验收。

首次验收的分钟测试可用合法 start API 建立1分钟隔离练习；不得修改正式 deadline 或修改系统时钟。截图的390px与缩短视口不是手机真机/软键盘测试。

## 11. 启动与日常使用

```sh
.venv/bin/ielts-reading serve --host 127.0.0.1 --port 8766
```

该命令使用项目现有配置与正式数据库；本轮没有重启正式服务。日常浏览 `/practice`，继续练习恢复草稿；从复习或成绩选择重新练习会新建记录。考试开始前检查时长与单篇说明，看到“已保存到服务器”才表示获得确认。进入候选稳定期后只修复实际复现的问题，保持现有布局。

## 12. 故障排查

- 未保存/请求超时：保持页面打开，恢复网络后立即保存；不要先刷新。检查 `/save` 状态码与服务器日志，不要输出 CSRF、密码或完整用户答案。
- 409 revision：保留本地答案用于人工对照，选择是否加载服务器版本；不能通过移除 revision 解锁。
- 409解析：先完成同篇活跃考试，重试解析；不能绕过服务端限制。
- 倒计时异常：比较 state 返回 deadline/server_time，检查服务器时间服务；禁止用前端改时来补偿正式成绩。
- 已交卷却仍在作答页：恢复连接并刷新指定 attempt，服务器应303到原成绩；检查 state.status，不新建记录冒充修复。
- 错题/解析差异：核对 attempt.unit_id、payload.answers 与正式 package；不要直接改数据库或正确答案。
- 数据异常：停止写操作，保留日志及只读快照；真实用户可能同时使用，不可用旧备份覆盖当前数据库。

## 13. 已知未验证范围

原生冲突确认框的取消/确认结果、实体手机软键盘/iOS/Android、安全退出最后一笔输入的设备差异、完整屏幕阅读器与全面WCAG审核、所有浏览器和长期持续使用体验尚未完整验收。关键键盘Enter/Space/方向键、ARIA当前题、核心文字对比度有本轮证据，不能外推为完整无障碍认证。

## 14. 安全回滚与冻结

本轮仅调整工作区小文字对比度及CSS缓存版本。根主报告记录开始时已存在的未提交修改和本轮增量；`outputs/ielts-final-acceptance/before/` 保存三个运行文件的逐字节基线，哈希已与开始快照匹配；`this-round.patch` 记录本轮差异，`stable-source.zip` 保存候选运行代码，`freeze-manifest.json` 标识哈希。

需要回滚时先保留当前代码，确认没有后续修改及活跃考试，停止 Web 服务；只恢复经哈希核对的三个基线文件，再启动并进行隔离回归。**不恢复数据库、不执行迁移、不删除资源、不用 git reset --hard 覆盖已有工作。** 文档可保留审计。

旧 `outputs/ielts-integration/rollback.py` 对后续P2修改会拒绝执行；其历史 --apply 分支还可能删除当时新增文件。本轮不运行，不再将它推荐为当前版本回滚入口。较大范围回退必须先单独审查代码兼容性与新学习记录，取得明确授权。

本轮不提交、不打标签、不改分支。候选代码身份见冻结manifest；可在用户审阅未提交改动后另行选择版本标签，不自动操作。
