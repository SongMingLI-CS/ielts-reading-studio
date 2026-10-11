# Learning Studio UI Pattern · 迁移验收

按用户指定顺序逐页修改，桌面截图检查后进入下一页。保留当前 `/practice` 页面结构和视觉；新增样式仅应用于 `.ls-page`。

桌面截图 1280 × 720；手机截图 390 × 844。当前真实数据下八个入口均已检查；技术学习和语境阅读当前分别没有文档、书籍，视觉验收覆盖入口和空态，已有内容分支仅有模板功能验证。写作截图覆盖编辑态，没有调用付费模型生成反馈。

相关既有功能测试 135 项通过。测试确认路由、筛选、分页、失败恢复、书库和配置等逻辑，不能替代截图验收。

## 1. 写作反馈 · 任务型

核心任务：作文编辑与反馈阅读。

紧凑题型选择，题目与作文独立输入区；保存及范文选项按需展开。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![写作反馈桌面](../outputs/ui-review/pattern-writing.jpg)

[手机截图](../outputs/ui-review/pattern-writing-mobile.jpg)

## 2. 复习 · 任务型

核心任务：错题回顾与词汇记忆。

题目和原文证据主导，题型统计次级展开；每道题保留轻微 surface。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![复习桌面](../outputs/ui-review/pattern-review.jpg)

[手机截图](../outputs/ui-review/pattern-review-mobile.jpg)

## 3. 知识库 · 阅读型

核心任务：词条检索与阅读。

移除总量看板，保留搜索；高级筛选收起，释义与例句纵向阅读。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![知识库桌面](../outputs/ui-review/pattern-knowledge.jpg)

[手机截图](../outputs/ui-review/pattern-knowledge-mobile.jpg)

## 4. 技术学习 · 阅读型

核心任务：学习路径与文档阅读。

有文档先显示文档；无文档展示学习路径，自定义导入按需展开。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![技术学习桌面](../outputs/ui-review/pattern-learning.jpg)

[手机截图](../outputs/ui-review/pattern-learning-mobile.jpg)

## 5. 语境阅读 · 阅读型

核心任务：书籍与章节。

书库与章节优先，估算和生成收进准备阅读章节。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![语境阅读桌面](../outputs/ui-review/pattern-novel.jpg)

[手机截图](../outputs/ui-review/pattern-novel-mobile.jpg)

## 6. 材料 · 数据型

核心任务：材料状态与下一步。

标题、规模和准备进度优先；解析字段与任务信息次级展开。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![材料桌面](../outputs/ui-review/pattern-materials.jpg)

[手机截图](../outputs/ui-review/pattern-materials-mobile.jpg)

## 7. 生成任务 · 数据型

核心任务：运行状态与恢复。

两列任务记录，完整材料名与可比较的状态、进度；手机纵向排列。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![生成任务桌面](../outputs/ui-review/pattern-jobs.jpg)

[手机截图](../outputs/ui-review/pattern-jobs-mobile.jpg)

## 8. 设置 · 配置型

核心任务：模型与工作空间。

分组标签、值、说明；必要行分隔，配置来源及维护说明按需展开。

桌面验收：标题和主任务先于辅助字段，沿用母版宽度、字体、主次动作、轻 surface。

![设置桌面](../outputs/ui-review/pattern-settings.jpg)

[手机截图](../outputs/ui-review/pattern-settings-mobile.jpg)

发布说明：本文截图属于本地验收证据，未随公开仓库发布；引用路径供验收机器查看。
