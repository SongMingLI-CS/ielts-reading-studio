# Learning Studio UI/UX 审计与改造记录

审查日期：2026-10-08。审查对象：FastAPI / Jinja 页面、`templates/`、`static/`，以及本地预览。仓库没有 React、Tailwind 或前端路由；路由由 `app/web/routes_*.py` 提供。现有认证由 `app/web/security_gate.py` 实现为 HTTP Basic 与会话，没有独立登录或注册页面。本报告中的“登录/注册页”因此标记为不适用，不虚构页面。

## A. 当前设计问题

### P0：导航与首页任务优先级

- `templates/base.html` 的横向 Header 容纳学习、内容管理、账户共 14 个导航/操作项。`static/experience.css:101-123` 通过 JS 增强和折叠菜单解决空间不足，用户必须寻找隐藏的“生成任务”“设置”。**改法**：桌面常驻低对比侧栏，手机使用明确的单个菜单入口；选中项只用浅背景与文字强调。
- `templates/home.html` 原有巨幅图像 Hero、英文口号、四张工具卡和横向轮播，主要行动“继续第 N 题”落在第二层。真实长标题和来源信息会在卡片中拥挤。**改法**：把可继续的练习置于页面第一任务，复习数量为次级行，工具入口改为五条列表行。

### P1：重复容器破坏扫描速度

- `static/studio.css` 的 `.product-card`、`.resume-card`、`.review-panel` 和 `static/experience.css` 的半透明覆盖同时使用圆角、边框、阴影。一个内容组往往拥有多重容器，读者需逐卡判断层级。**改法**：首页只保留当前任务的独立容器，其余用分隔线和行。
- `static/practice-center.css` 的 `.practice-hero` 有主 Hero、独立学习概览、概览内四张统计卡；可练习篇目又是双列卡。**改法**：概览压成一行数据，篇目改连续列表。
- `static/app.css` 的 `.question` 每题使用带边框圆角块，连续作答像一组弹窗。**改法**：问题采用开放阅读流和细分隔线，保留选中与错误状态。
- `templates/writing/index.html` 的 Loading 由标题卡与四个骨架卡组成，反馈结果又拆成多块等权面板。**改法**：Loading 只表明当前阶段；反馈使用标题、分隔线和正文层级。

### P2：视觉系统和状态反馈

- `static/tokens.css` 有基础色，但 `static/app.css`、`static/studio.css`、`static/technology.css`、`static/experience.css` 多次覆盖同一组件；同一“页面主区”会随着载入顺序改变背景和字体。**改法**：建立语义 Token 和最后统一的产品样式层，旧变量只作兼容别名。
- `templates/jobs/index.html` 的任务本身适合高密度表格，但筛选做成胶囊按钮、空态加装饰星；`templates/jobs/detail.html` 的错误信息与日志容易争夺注意力。**改法**：筛选变文字 Tabs，错误面板以恢复步骤为主，表格维持行密度。
- `templates/settings/index.html` 的模型配置按一整页 Card 加子行展示，状态、文件路径和说明同级。**改法**：区块标题加行，关键状态和测试连接保留独立操作区。

### P3：装饰与文案

- 英文 eyebrow（如 `READING / PRACTICE`）、“探索下一章”等营销文案与大量非业务图片让本地学习工具像落地页。首页改为具体学习任务和真实材料数量。
- 旧首页的“规划中”工具占一张完整卡，挤压已可用的功能入口。现放入已有的技术学习工作区，不再参与首页主入口排序。

## B. AI 模板风格专项

| 特征 | 现状与代码位置 | 处理 |
|---|---|---|
| 过多 Card、Card 套 Card | `home.html` 的工具卡、`practice/index.html` 的概览卡内四张小卡、`writing/index.html` 的结果卡 | 首页改行；概览改紧凑数据；写作反馈改阅读流 |
| 大圆角、阴影、边框 | `static/studio.css` `.product-card` / `.workspace-card`；`static/experience.css` `.resume-card` | 统一 4/8/12px 半径；独立内容才保留边框；常规区块不用阴影 |
| 紫蓝渐变、玻璃、glow、模糊 | `static/experience.css:3-40,68-80` 的环境背景、浮动 Header、Hero、spotlight | 新产品层取消环境层、玻璃和浮动动效 |
| 无意义图标容器、Lucide 泛滥 | 未发现 Lucide 系统；旧空态使用装饰性 `✦` | 隐藏装饰符号，保留文字与操作 |
| 大标题与大留白 | 旧首页 Hero `templates/home.html`、旧阅读 Hero `practice/index.html` | 标题收敛至 30px，页首服务于当前任务 |
| 胶囊按钮与独立背景色 | `static/app.css` `.filter-tabs a`、统计标签 | Tabs 使用底线选中态；统计行使用文本 |
| 过度动画、hover scale、浮动元素 | `static/experience.js` 的入场动画/spotlight、`experience.css` 图片缩放与漂移 | 停用旧脚本，只保留菜单状态切换 |
| SaaS 落地页文案 | 旧首页英文口号、工具轮播与“下一步”展示 | 使用真实学习入口、待办和材料数量 |
| 普通聊天气泡 Agent | 本项目没有聊天式 Agent UI；`jobs` 是批量生成任务 | 保留阶段、状态、失败原因和重试操作，不添加聊天界面 |

其余逐项核对：渐变文字未发现；Lucide 图标泛滥未发现；无意义图标盒主要是品牌标记和空态符号，空态符号已移除。旧首页的轮播与四张等权产品卡具有 SaaS 落地页模板感，旧阅读概览具有 Dashboard 模板感，均已收敛。旧 `experience.css` 仍保留兼容规则，但不再加载其入场动画脚本，新的产品层覆盖其主要背景、阴影与模糊效果。

## C. Benchmark

以下只借鉴信息组织和交互原则，不复制品牌外观。资料均为各产品官方说明。

| 产品 | 参考部分与原因 | 不照搬 |
|---|---|---|
| [Linear](https://linear.app/docs/custom-views) | 侧栏保存稳定视图；列表里把对象名称作为第一视觉层，筛选在上下文内 | 不复制其快捷键密度和工程团队术语 |
| [Notion](https://www.notion.com/help/layouts) | 内容与属性可分层显示，低频详情收纳到附属区 | 不引入可配置数据库或复杂块编辑器 |
| [Claude Projects](https://support.claude.com/en/articles/9519177-how-can-i-create-and-manage-projects) | 项目知识与对话分区，来源是明确的工作对象 | 不改造成本项目并不存在的聊天工作流 |
| [GitHub Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/changing-the-layout-of-a-view) | 任务以高密度表格呈现，字段、筛选和状态服务于扫描 | 不搬入看板/路线图等无关模式 |
| [Raycast](https://manual.raycast.com/quicklinks) | 高频入口易找、名称直接对应行动 | 不复制命令面板作为唯一导航 |

## D. 新设计语言

**专业、安静、紧凑、内容优先。** 首页先回答“下一步做什么”，练习页先让人找到文章，生成任务页先让人判断状态。侧栏为背景层，主工作区为视觉主体。颜色只承担行动与状态意义；边框用于表格、独立任务和强分组；阴影不承担信息层级。

视觉层级：Level 1 当前任务或文章；Level 2 主列表/正文；Level 3 页面内操作；Level 4 来源、时间、数量；Level 5 备份与高级配置。

## E. Design Tokens

定义见 `static/tokens.css`。字号：Display 30–42px / H1 30px / H2 21px / H3 16px / Body 15px / Small 13px / Label 与 Caption 12px / Metadata 11px / Code 使用系统等宽字体。字重常规 400–500、标题 650–680；正文行高 1.55–1.6，中文标题字距约 -0.025em。

间距阶梯：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48px。圆角：small 4px、medium 8px、large 12px。语义颜色：background、surface、surface-hover、surface-selected、三档 border、三档 text、accent、success、warning、danger。旧 CSS 变量是兼容别名，不再作为新组件的设计入口。

## F. 页面改造优先级

- **P0**：全站导航、首页当前任务和空态。
- **P1**：阅读篇目、作答区域、生成任务、写作评估反馈。
- **P2**：语料/知识库/技术学习、设置和备份的分组密度。
- **P3**：装饰性文案与旧媒体资源清理。

## G. 修改文件与验证

本轮修改 `static/tokens.css`、`static/product-system.css`、`static/product-system.js`、`templates/base.html`、`templates/home.html`，并调整阅读、写作、任务、审阅与设置模板的文案及任务异常提示；对应更新 `tests/web/test_home_dashboard.py` 与 `tests/web/test_ui_contracts.py`。旧业务路由与存储逻辑未改。

浏览器验收：用本地真实材料与任务预览首页、阅读、写作、生成任务、材料、设置、技术学习、语境阅读和知识库，在 1440 / 1280 / 1024 / 768 / 390px 共 45 个组合检查主标题存在与横向溢出，均通过。另在 768px 验证任务列表从拥挤表格变为标签行，在 390px 验证真实长篇名换行和操作可见。手机菜单展开及 Escape 收起通过。独立登录/注册页不适用；写作 Loading、任务失败和空态已通过模板及样式审查，其中真实模型 Loading 未触发外部 API。完整测试 `780 passed`。

应用目前没有深色模式切换或深色主题契约，本轮只验证浅色主题的对比度；未将系统深色偏好误当成一个已支持的产品功能。
