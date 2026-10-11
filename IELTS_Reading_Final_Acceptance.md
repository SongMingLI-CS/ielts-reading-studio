# IELTS Reading Final Acceptance

验收日期：2026-10-11。结论：**日常使用候选稳定版，保留明确的人工待测项**。

本轮使用真实 FastAPI/Jinja/JavaScript 与正式评分代码，所有作答均在新建隔离库执行。未运行历史原型作为正式验收；未删除文件、修改 schema、写入正式学习记录、提交 Git 或创建标签。

证据目录：`outputs/ielts-final-acceptance/`。运行机制统一维护在 [模块技术文档](docs/ielts-reading-integration.md)。旧整合、就绪性、P2报告保留为历史证据，其成功声明没有直接计入本轮结果。

发布说明：本报告记录验收冻结时的状态。随后用户已授权推送与部署；发布门禁另执行 `scripts/verify.sh`，809项测试、Ruff、字节编译与CLI检查通过，并修正两处不影响行为的导入问题。`outputs/`证据、SQLite、备份和临时原型保留本地，不上传公开仓库；下文证据链接供验收机器本地查看。

## A. 项目最终状态

### 可正常使用

- `/practice` 浏览文章、开始和继续练习；正式入口使用已有路由。
- Study：八种题型、独立两栏、题型分组、当前题、复查、高亮、18–22px字号、保存/刷新恢复。
- Exam：明确标注单篇限时，服务器启动/截止时间，刷新不重启，截止后按服务器最后保存答案交卷。
- 服务器评分、已交卷终态、中文解析、原文段落证据、累计错题复习、重新练习建立新记录。
- revision冲突拦截、明确保存失败反馈、提交响应丢失后的终态同步、重复提交幂等。
- 同篇活跃考试阻止全篇解析，409有原因及可执行下一步。

### 证据边界

本轮没有复现P0/P1阻断问题。候选结论适用于**个人日常单篇训练**，不是完整官方考试产品或全面无障碍认证。原生冲突确认框取消/确认的浏览器结果仍未验证，不能宣称全部人工验收完成。

### 历史状态复核

历史整合报告的203项、就绪性报告和P2报告只用于确定检查路径。通过源码确认业务确实存在，再重新运行全项目809项测试、正式模板浏览器流程、数据库快照。历史1分钟超时和八题型成绩均在本轮另建新记录重复验证。

## B. 自动化测试

本轮实际执行两次完整测试，无删断言、放宽权限、修改答案或跳过核心测试：

```sh
.venv/bin/python -m pytest -q --junitxml=outputs/ielts-final-acceptance/pytest.xml > outputs/ielts-final-acceptance/full-tests.log 2>&1
.venv/bin/python -m pytest -q --junitxml=outputs/ielts-final-acceptance/pytest-final.xml > outputs/ielts-final-acceptance/full-tests-final.log 2>&1
```

- 初次：809通过，0失败，0跳过，32.97秒。
- 局部修复后：**809通过，0失败，0错误，0跳过，32.94秒**。
- 唯一警告：Starlette TestClient使用已弃用的anyio BlockingPortal别名。未导致功能失败，未为去警告升级依赖。
- 测试包含258项web、106项security、24项storage；阅读核心30、阅读工作区27、阅读列表6、复习6、UI契约23（契约覆盖全站，不全部属于阅读）。总数809为全项目数量，不能称全部都是IELTS阅读测试。
- `tests/web/conftest.py` 使用tmp_path AppConfig/SQLite；其他现有测试也采用隔离fixture。本轮未把正式config数据库路径传入作答测试。

覆盖八题型控件与原评分、Study/Exam、保存/恢复、fresh语义、提交与重复提交、结果/复习、服务端deadline、迟到答案、双标签/并发写reservation、CSRF/权限/foreign-attempt规则及P2业务回归。浏览器导航行为另外实测，未用模板字符串断言替代真实滚动。

证据：[最终JUnit](outputs/ielts-final-acceptance/pytest-final.xml)、[最终日志](outputs/ielts-final-acceptance/full-tests-final.log)、[统计](outputs/ielts-final-acceptance/test-summary.json)。

## C. 浏览器验收

### 环境与真实流程

- 新隔离正式代码服务：127.0.0.1:8894；故障注入代理：8895，仅转发到8894。
- 数据：`outputs/ielts-final-acceptance/runtime/state.db`。启动脚本显式断言与正式数据库路径不同。
- 12题实文：**Characters and Connections in the Lost Tomb Series**，805 words，A–H段。只读复制现有正式package到隔离输出，未重生成或改动正式材料。
- 八题型：`tests/fixtures/reading/workspace_types.json`，正式数据模型/控件/评分器。该夹具验证交互，不代表正式考题内容质量。
- 浏览器使用受支持CUA操作真实控件与滚轮；DOM评估只读，不注入答案或修改页面业务状态。

实际完成：书架 → 开始 → 题型切换 → 阅读定位 → 复查 → 修改答案 → 保存 → 刷新 → 恢复 → 补完 → 提交 → 10/12成绩 → 中文解析 → 段落D证据 → 两道错题复习 → 重新练习。

- 八种题型另行作答、保存、刷新、提交：**8/8**；所有八个答案均恢复。
- 12题故意错第4、7题：**10/12**；错题本实际显示这两题，含提交答案、正确答案、中文说明与原文证据。
- 重新练习得到空白新attempt；原草稿第1题仍为i、历史10/12记录保持不变（只读隔离库与API复核）。
- 高亮按当前段落添加，字号18→19，服务器保存后刷新，mark仍在、字号仍为19px。选中任意跨节点文字的所有边界情况未穷尽。
- 学习没有强制deadline；考试启动说明/倒计时/提交前隐藏本次答案与解析已检查。考试刷新19:23→19:23，没有恢复到20:00。
- 一分钟测试通过合法start API建立隔离考试，服务器真实时间流逝，无改系统时钟或直接改deadline。浏览器倒计时后自动进入“限时结束 · 已交卷”：**1/8，60秒，timed_out=true**。
- 按同一已交卷ID再次submit（尝试不同答案）：200返回原10/12；整条记录逐字节未变。已交卷save返回409，原练习入口303到原成绩。

### 导航、键盘与五尺寸

最终CSS版本下重跑 `tests/browser/practice_p2_regression.mjs`：**Study 10组 + Exam 10组 + Result 2组 = 22组通过**。

- 点击第6题后左栏滚动：当前仍6，右栏位置不变。
- 右栏可见输入焦点保留当前题，移出可见区后题号跟随；选择/聚焦更新当前题。
- 刷新恢复答案、当前题、复查及文章位置。
- 1440/1280/1024/768/390px：从TFNG回Matching时标题与完整说明均在题区可见范围，焦点为题型标题；题号仍可直接进入问题。
- 解析点击4立即更新；滚动后与主要可见题一致；Enter定位7，唯一`aria-current=step`。1280/390均实测。
- 手机切换文章/题目保留当前题和题区滚动位置。
- 原生radio：Enter到第4题，Space选TRUE，ArrowRight改FALSE；焦点与当前题一致。分栏拖动器键盘ArrowRight令53→55。
- 五宽度document.scrollWidth等于innerWidth，无页面横向溢出。输入第12题底部始终在题区内、操作栏上方。
- 390×420缩短视口：输入底部296.1，题区底部354，操作栏顶部365，未遮挡。**这是视口模拟，不是真机软键盘验证。**
- 最终页面查询浏览器error/warn日志为空；故障注入期间预期的请求失败不算普通运行异常。

### 网络与冲突

1. 保存返回HTTP503：输入`unsaved network test`仍在，状态明确“未保存的答案仍在本页，请重试”。
2. 保存连接被代理关闭：输入`transport unsaved`仍在，提示“无法连接服务器…未保存”。恢复连接、改为sea tomb后显示“已保存到服务器”。这是真实请求失败模拟，不是实体网络断开测试。
3. submit已成功处理，但代理丢弃响应：页面随后同步服务器状态，进入原10/12结果，不生成第二条成绩。
4. 两标签同一attempt：A保存i后，B以旧revision保存ii得到409；B输入ii留在页面，提供重新加载按钮、禁止交卷。
5. 同篇活跃Exam：历史成绩的全篇解析点击后显示409对话框、原因、返回限时练习/重试；Exam交卷后全篇解析正常打开。

### 原生确认框：优先补验但未完成

实际点击冲突后的重新加载按钮，浏览器打开原生confirm：“重新加载会舍弃本页尚未保存的修改。确认加载服务器版本？”

随后工具读取原生弹窗超时，原生Codex应用控制被安全策略拒绝。没有绕过限制，也没有把弹窗打开当作取消/确认通过。源码检查显示取消分支不改dirty、不导航；确认才清dirty并加载服务器草稿，这只能作源码证据。

**人工待测（优先）：**

- 两标签打开同attempt；A保存i，B改ii后保存产生409。
- B点重新加载 → 取消：ii仍在、URL不变、冲突提示仍在，未偷偷加载i。
- 再点重新加载 → 确认：URL保持同attempt，重新显示服务器i；没有修改历史成绩。

该两分支不计入22组通过，不声称完整浏览器验收全部通过。

### 截图与机器证据

![最终Study桌面](outputs/ielts-final-acceptance/study-final-1440.png)

![最终Study手机尺寸](outputs/ielts-final-acceptance/study-final-390.png)

其余实际截图：

- Study：[1280](outputs/ielts-final-acceptance/study-final-1280.png)、[1024](outputs/ielts-final-acceptance/study-final-1024.png)、[768](outputs/ielts-final-acceptance/study-final-768.png)。
- Exam：[1440](outputs/ielts-final-acceptance/exam-final-1440.png)、[1280](outputs/ielts-final-acceptance/exam-final-1280.png)、[1024](outputs/ielts-final-acceptance/exam-final-1024.png)、[768](outputs/ielts-final-acceptance/exam-final-768.png)、[390](outputs/ielts-final-acceptance/exam-final-390.png)。
- 成绩解析：[桌面](outputs/ielts-final-acceptance/result-final-1280.png)、[手机尺寸](outputs/ielts-final-acceptance/result-final-390.png)；[错题复习](outputs/ielts-final-acceptance/review-mobile.png)、[全篇解析](outputs/ielts-final-acceptance/full-analysis-mobile.png)。
- [保存HTTP503](outputs/ielts-final-acceptance/network-503.png)、[连接中断](outputs/ielts-final-acceptance/network-disconnect.png)、[冲突提示](outputs/ielts-final-acceptance/revision-conflict.png)、[解析409](outputs/ielts-final-acceptance/analysis-409-mobile.png)、[提交确认](outputs/ielts-final-acceptance/submit-mobile.png)、[一分钟交卷](outputs/ielts-final-acceptance/minute-result.png)、[缩短视口](outputs/ielts-final-acceptance/short-viewport.png)。
- [最终Study回归](outputs/ielts-final-acceptance/study-regression-final.json)、[Exam回归](outputs/ielts-final-acceptance/exam-regression-final.json)、[Result回归](outputs/ielts-final-acceptance/result-regression-final.json)、[完整流程记录](outputs/ielts-final-acceptance/browser-flow.json)、[隔离库/API证据](outputs/ielts-final-acceptance/isolated-final-proof.json)。

早期重跑曾误将视口作用于另一个标签，手机控件不可见；另一次复用已答TRUE的草稿使check()不触发焦点，测试前提失效。均改用当前标签、全新空白草稿后重跑，没有改断言或产品逻辑。只有带final后缀的22组回归计入最终状态。

## D. 数据安全

正式库以SQLite URI `mode=ro`、一致性读事务记录前后快照；没有创建正式测试练习或调用其可能结算的state/作答路由。

- schema：**0004 → 0004**，schema SQL指纹一致。
- 重要表：**19 → 19**，全部19张表的行数与行内容指纹逐一一致。
- 练习记录：**3 → 3**。
- 练习整体SHA-256：`66bffd075a45e3154659c28e4e27bbeba8dd5bfe308a37e532649cbcc22b38bc`，前后一致。
- 活动练习：前后同一条study草稿，legacy revision/deadline均null。未自动升级、提交或覆盖该记录。
- 前后quick_check=ok；foreign_key_check均0条问题。
- schema迁移仅发生在新隔离库按已有迁移创建之时；正式库未迁移，正式schema代码未修改。

[开始快照](outputs/ielts-final-acceptance/production-before.json)、[结束快照](outputs/ielts-final-acceptance/production-after.json)、[比较结果](outputs/ielts-final-acceptance/production-comparison.json)。快照仅输出计数/哈希及活动记录身份，不含用户答案或凭据。

## E. 五项P2修复复核

- **P2-01 作答题号同步：已复核。** 原路径点击6后仅滚动文章误变5；旧滚动监听混合两栏更新。现有practice.js只在右栏更新当前题并尊重可见输入焦点。Study/Exam两模式、刷新和五尺寸回归通过。本轮未重写该实现。
- **P2-02 解析同步：已复核。** 原路径滚至9–11仍强调4；旧结果脚本只有点击同步。现有脚本根据主要可见题同步，程序滚动有保护，点击即时唯一aria-current。桌面/手机、Enter均通过。
- **P2-03 题型说明遮挡：已复核。** 原路径TFNG回Matching直接聚焦第一题，把说明滚出视口。现有题型跳转定位题组/聚焦标题，使用滚动容器已有offset；题号跳转仍进具体题。两模式五宽度完整说明检查通过。
- **P2-04 重做语义：已复核。** 原错题重做入口默认resume，返回旧草稿。现有入口fresh=1并指定mode，不覆盖旧记录；错题本新空白练习与原i草稿/历史成绩保留已验证，后端自动化覆盖结果重做模式。
- **P2-05 解析入口409反馈：已复核。** 原直接导航409没有清楚反馈；现有受保护GET探测与对话框解释限制。实际活跃Exam显示阻止原因与下一步，交卷后正常进入解析；考试规则没有放宽。

原生冲突确认的两个结果分支是另一个人工待测项，不能混入P2-01/02的已验证结论。

## F. 本轮修改与维护审查

### 实际增量

- `static/ielts-workspace.css`：仅加深工作区小状态文字（保存、进度、题型、复查、辅助动作）在既有背景上的颜色；不改Token、字号、比例或布局。
- `templates/practice/session.html`、`result.html`：仅CSS查询版本v1→v2，避免缓存旧样式；JS仍为practice v7/result v2。
- `docs/ielts-reading-integration.md`：替换过时汇总，统一代码依据、运行机制、限制、排查与安全回滚。
- 新增本主报告及`outputs/ielts-final-acceptance/`隔离测试、只读快照、截图、增量基线、冻结manifest。未改其他业务模块。

### 确认的低风险缺陷与验证

保存状态对比度3.953:1、选中题型4.090:1；检查发现同一弱文字颜色也用于进度/辅助动作。针对这些已存在surface调整文字色，仍是暖灰/陶土/赭色。

修复后实际computed-style采样：保存/进度/辅助动作4.983:1、选中题型及其子标签4.831:1、普通题型标签5.497:1、复查5.412:1；正文/题干13.122:1、说明7.029:1、主按钮4.920:1。并非所有文本、hover状态与辅助技术的全面WCAG认证。

[修复前采样](outputs/ielts-final-acceptance/contrast.json)、[修复后采样](outputs/ielts-final-acceptance/contrast-final.json)、[本轮运行代码增量](outputs/ielts-final-acceptance/this-round.patch)。完整测试与22组浏览器回归在修复后重跑通过。

### 维护审查结论

阅读脚本、结果脚本、请求封装、模板缓存、状态路由与原子仓库已检查：脚本按页面只加载一次；保存串行且revision在服务器事务检查；Promise请求错误有捕获；解析请求有abort/超时清理；提交先保存，终态锁定；当前题ARIA唯一。没有复现需要重构的重复交卷、失效DOM或未处理Promise问题。

1秒计时/10秒保存同步随页面生命周期运行；visibilitychange尝试保存，离页不能保证最后请求成功，属于已记录边界。保留已有CSS中历史原型选择器，不凭“暂未引用”删除。未改评分器、后端模型、安全规则或数据库结构。

## G. 已知限制与风险分层

### 已支持

单篇学习、单篇服务器限时、八题型、草稿持久化、成绩与错题复习、中文解析、段落定位、revision和CSRF保护。

### 部分支持/实际风险

- 网络失败后答案只在当前页内存，刷新会丢失未确认输入；界面已明确提示，尚无离线持久机制。
- 超时依赖后续state/save/submit等请求结算，离线或关闭所有标签时不会后台立即写终态。
- 历史结果/本地package仍可查看；考试保护不是防作弊隔离环境。
- 证据定位到段落，未逐句自动定位；AI题目质量需人工判断。
- 页面退出保存是best effort，不保证进程强制关闭或设备网络中断的最后输入。

### 尚未验证，不能算已复现缺陷

- 原生冲突confirm取消/确认后的真实浏览器输入保留与服务器恢复（优先人工补验）。
- iOS/Android实体设备、真实软键盘、长时间使用疲劳、完整屏幕阅读器、跨浏览器全面兼容与全面无障碍。
- 当前视口测试和核心键盘测试只支持局部结论。

### 未支持，本轮不开发

三篇/40题完整模考、完整考试统一交卷、Matching Features、Table/Note Completion、离线持久草稿、无请求后台结算、AI生成题目系统性质量审计。

## H. 清理候选（仅分类，零删除）

[目录数量/体积实测清单](outputs/ielts-final-acceptance/inventory.json)，记录的是盘点时状态，不会自动执行清理。

- **A 正式运行必需：** app阅读路由/schema/model/storage/security、practice模板、正式static、migrations、config及真实input/output/package/database。保留；配置和用户资料不是清理目标。
- **B 自动化测试必需：** tests/web阅读/安全/UI契约、tests/security、storage、八题型fixtures及浏览器回归mjs。新隔离启动与故障代理脚本保留用于复现；不要与正式服务混用。
- **C 回滚/审计证据：** outputs/ielts-integration（盘点59文件）、ielts-readiness（19）、ielts-p2-fixes（28）、本轮截图/日志/快照/before/patch/冻结代码；private-ielts-rollback文件需按敏感备份保管，不打开或删除。旧报告不更新成假装本轮结果。
- **D 可以归档：** outputs/visual-directions（106文件，A–E及IELTS原型/研究截图）、outputs/ui-review（98文件）、历史临时预览页/调试日志、已完成隔离runtime DB。建议以后经确认移到只读归档，保留引用和复现说明；本轮不移动或删除。
- **E 疑似废弃，需确认：** 工作区CSS中的旧.workspace/.mobile-tabs/#save-status/.duration等选择器、早期浏览器cjs脚本、重复历史报告/预览入口。未证明无历史依赖；不删除。旧rollback.py只作历史证据，不执行其apply分支。

正式SQLite WAL/SHM不属于可删临时文件；隔离DB也应与证据一同归档。旧预览服务器是否仍供用户对照不明确，本轮未终止它们。本轮8894/8895隔离测试服务及三个临时浏览器标签已关闭，数据库和全部证据保留。

## I. 运行、测试与安全回滚

正常启动：

```sh
.venv/bin/ielts-reading serve --host 127.0.0.1 --port 8766
```

使用 `/practice`；继续恢复草稿，重新练习新建记录。看到“已保存到服务器”才获得成功确认。正式serve启动会使用原项目已有迁移机制，本轮未重启正式服务或执行正式迁移。

复现隔离环境：

```sh
PYTHONPATH=. .venv/bin/python outputs/ielts-final-acceptance/serve_isolated.py
# 可选的隔离故障代理；8895只指向8894
.venv/bin/python outputs/ielts-final-acceptance/fault_proxy.py
```

测试全套使用B节命令；浏览器回归需当前CUA标签与viewport，Study/Exam从全新草稿开始。隔离runtime保留，不删除重置它来制造干净结果；再次验收要新建独立路径或新的attempt。

排查先看HTTP/status/服务器日志；409不能移除revision或放宽解析限制；断网时保持页面输入并重试。数据异常停止写入、保留只读证据，不用旧DB覆盖可能新增的真实学习记录。

本轮安全回退仅涉及三个运行文件。`before/`文件哈希已与开始时快照匹配；停止Web、核对无活跃考试和后续代码差异、备份当前文件后才可人工恢复。**不回退数据库、不执行schema迁移、不删资源、不reset未提交工作。** 本轮没有执行回滚。

历史integration rollback.py会因P2后续修改拒绝；不能修改guard绕过检查，其apply还含历史删除逻辑。较大回滚需另外审查和确认。具体排查/安全要求见统一技术文档。

## J. 稳定版结论与冻结

### 结论

**可进入个人日常试用，标记“日常使用候选稳定版”。**

正常单篇学习、单篇服务器限时、草稿/成绩可靠保存和错题复习链路均有新实测；正式库前后不变，没有复现P0/P1阻断。局部可读性P2已修复。原生confirm两分支与真机/完整无障碍仍人工待测，故不是“全部场景已认证”的正式完结声明。

### 代码身份

- 当前分支：`codex/technical-learning`。
- HEAD：`5c784c2c23de01313c170dc96805a9d93311686c`，**这个提交不包含当前全部工作区变更**。
- 本轮开始已有26条tracked dirty、230条untracked文件记录。包括先前阅读整合/P2及其他页面修改，本轮未将其当作新改动、未清理或覆盖。
- 开始文件哈希/Git状态：[code-before.json](outputs/ielts-final-acceptance/code-before.json)；冻结文件哈希/增量：[freeze-manifest.json](outputs/ielts-final-acceptance/freeze-manifest.json)；候选阅读运行代码：[stable-source.zip](outputs/ielts-final-acceptance/stable-source.zip)。压缩包不含真实DB、用户资料或.env，不是完整项目部署包。
- 本轮相对开始基线仅改变CSS、session/result缓存版本及统一技术文档，另新增本报告和证据目录。没有提交、打标签或改变分支。
- 如以后需要Git标签，先审阅已有未提交工作，再由用户选择提交范围和标签；可考虑 `ielts-reading-daily-candidate-2026-10-11`，本轮未创建。

### 冻结规则

保持当前阅读布局与设计语言。后续只修复实际复现缺陷；暂停UI重构、反复换布局和主动增加新功能。本轮结束后停止开发。人工待测清单保留，不通过继续堆自动化次数掩盖它。
