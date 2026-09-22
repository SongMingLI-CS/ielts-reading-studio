"""Plain-language explanations for the generation options shown in the web UI."""

from __future__ import annotations

from app.models import Difficulty, QuestionType

# 任务状态的中文说法。任务列表与材料卡都要显示它，放在一处避免两页说法不同。
# 键必须与 app.pipeline.queue 里的状态常量一致：库里存的是 completed /
# completed_with_errors / blocked，直接显示会把内部枚举漏给用户（测试守着这点）。
JOB_STATUS_LABELS = {
    "queued": "排队中",
    "running": "生成中",
    "paused": "已暂停",
    "blocked": "已阻塞",
    "completed": "已完成",
    "completed_with_errors": "部分失败",
    "failed": "已失败",
    "cancelled": "已取消",
}
#: 与 queue.ACTIVE_STATUSES 对齐："还有人打算把它做完"的状态。
ACTIVE_JOB_STATUSES = ("queued", "running", "paused", "blocked")
#: 真实作业类型见 queue.READING_KIND / SAMPLE_KIND / NOVEL_KIND。
JOB_KIND_LABELS = {
    "reading_generation": "阅读生成",
    "reading_sample": "样章生成",
    "novel_component": "小说组件",
}

#: 队列与 worker 的错误码 → 人话。页面只显示「handler_failed:RuntimeError」等于什么都没说：
#: 操作者既不知道是哪一类问题，也不知道下一步该看哪儿。新增错误码时补在这里。
JOB_ERROR_LABELS = {
    "unknown_job_kind": "认不出的作业类型（通常是升级或回滚留下的旧作业）",
    "worker_lease_expired": "worker 租约过期，作业被回收后重排",
}
JOB_ERROR_HINTS = {
    "unknown_job_kind": "确认代码版本和作业类型对得上，再提交一次；这个作业不会自己重试。",
    "worker_lease_expired": "一般会自动重跑；反复出现说明 worker 被反复重启，先看服务是否稳定。",
}
#: `handler_failed:` 后面跟的是 Python 异常类名，单独成句才能指出错在哪一层。
HANDLER_FAILED_LABEL = "组件抛出了异常"
HANDLER_FAILED_HINT = (
    "异常来自生成组件本身：下面列出这次运行的摘要，完整堆栈在 worker 日志里。"
)
#: 小说组件 run_status.json 的 outcome → 人话（任务页与情境小说页共用一套说法）。
NOVEL_OUTCOME_LABELS = {
    "completed": "本次全部成功",
    "no_chapters": "本次没有章节通过质量检查",
    "failed": "本次有章节失败",
}


def job_error_label(code: str | None) -> str:
    """错误码的中文说法；`handler_failed:RuntimeError` 会点出具体的异常类名。"""

    if not code:
        return ""
    if code.startswith("handler_failed:"):
        return f"{HANDLER_FAILED_LABEL}（{code.split(':', 1)[1]}）"
    return JOB_ERROR_LABELS.get(code, code)


def job_error_hint(code: str | None) -> str:
    if not code:
        return ""
    if code.startswith("handler_failed:"):
        return HANDLER_FAILED_HINT
    return JOB_ERROR_HINTS.get(code, "")


#: 阶段名 → 人话。任务页说「哪一步失败」时不能只给 author_passage 这种内部名。
STAGE_LABELS = {
    "author_brief": "写作：文章提纲",
    "author_passage": "写作：生成 Passage",
    "author_passage_revision": "写作：修订 Passage",
    "examiner_passage_review": "出题：复核 Passage",
    "examiner_assessment": "出题：题目与解析",
    "examiner_assessment_revision": "出题：修订题目",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage or "生成")


#: 单元状态 → 人话。任务表的「状态」列此前直接显示 indexed / needs_review 这类内部枚举。
UNIT_STATUS_LABELS = {
    "indexed": "待生成",
    "author_generating": "正在写 Passage",
    "passage_reviewing": "正在复核 Passage",
    "examiner_generating": "正在出题",
    "validating": "正在校验",
    "completed": "已完成",
    "author_revision_required": "需要重写 Passage",
    "examiner_revision_required": "需要重出题",
    "needs_review": "待人工确认",
    "failed": "失败",
    "paused": "已暂停",
    "cancelled": "已取消",
}


def unit_status_label(status: str) -> str:
    return UNIT_STATUS_LABELS.get(status, "状态未知")


def job_status_label(status: str) -> str:
    """状态的中文说法；未知状态说"状态未知"，而不是把英文枚举漏到界面上。"""

    return JOB_STATUS_LABELS.get(status, "状态未知")


def job_status_class(status: str) -> str:
    """给状态徽章用的 CSS 类名后缀（completed_with_errors → completed-with-errors）。"""

    return status.replace("_", "-") if status in JOB_STATUS_LABELS else "unknown"


def job_kind_label(kind: str) -> str:
    return JOB_KIND_LABELS.get(kind, kind or "任务")

QUESTION_TYPE_GUIDE: dict[QuestionType, dict[str, str]] = {
    QuestionType.MATCHING_HEADINGS: {
        "name_zh": "小标题匹配",
        "what": "给每个段落从备选小标题里挑一个最贴切的；备选数量多于段落数，用来避免靠排除法作答。",
        "example": "Paragraph A → viii. 主角的背景与作用",
        "grading": "按小标题序号判定（如 viii），同一个小标题不会重复使用。",
    },
    QuestionType.TRUE_FALSE_NOT_GIVEN: {
        "name_zh": "事实判断（TRUE / FALSE / NOT GIVEN）",
        "what": "判断陈述与原文是否一致：一致 TRUE，矛盾 FALSE，原文根本没有提到 NOT GIVEN。",
        "example": "Wu Xie has decades of experience. → FALSE",
        "grading": "三选一，大小写和空格不敏感。",
    },
    QuestionType.YES_NO_NOT_GIVEN: {
        "name_zh": "观点判断（YES / NO / NOT GIVEN）",
        "what": "只针对作者的观点或主张，而不是客观事实：观点一致 YES，相反 NO，未表态 NOT GIVEN。",
        "example": "The author thinks the plan is risky. → YES",
        "grading": "三选一；NOT GIVEN 必须给出「缺少什么信息」的说明。",
    },
    QuestionType.MATCHING_INFORMATION: {
        "name_zh": "信息定位匹配",
        "what": "题干描述某条信息，要求选出它出现在哪个段落（A–H）。",
        "example": "A description of the tools they used → 段落 C",
        "grading": "按段落字母判定。",
    },
    QuestionType.MULTIPLE_CHOICE: {
        "name_zh": "单选题（A/B/C/D）",
        "what": "四选一，选项由模型针对该题生成，每个错误选项都必须给出「为什么不选」的解释。",
        "example": "Why did the plan fail? → B. 因为地下水提前涌出",
        "grading": "按完整选项文本判定（不是字母）。",
    },
    QuestionType.SENTENCE_COMPLETION: {
        "name_zh": "句子填空",
        "what": "用原文里的词补全一个句子，受「NO MORE THAN N WORDS」限制。",
        "example": "They used steel pipes to ______ for the tomb. → probe",
        "grading": "答案必须是原文原词，忽略大小写与多余空格。",
    },
    QuestionType.SUMMARY_COMPLETION: {
        "name_zh": "摘要填空",
        "what": "用原文里的词补全一段摘要（通常 4 题一组），同样受词数限制。",
        "example": "…he lacks ______ skills. → professional",
        "grading": "同句子填空：原文原词、忽略大小写。",
    },
    QuestionType.SHORT_ANSWER: {
        "name_zh": "简答题",
        "what": "用原文里的词或短语回答一个直接问题，受词数限制。",
        "example": "What did he swap with Wu Sansheng? → identities",
        "grading": "原文原词，忽略大小写。",
    },
}

DIFFICULTY_GUIDE: dict[Difficulty, dict[str, object]] = {
    Difficulty.FOUNDATION: {
        "name_zh": "Foundation 基础",
        "questions": 10,
        "note": "句子较短、词汇常见，适合先找手感。",
    },
    Difficulty.STANDARD: {
        "name_zh": "Standard 标准",
        "questions": 12,
        "note": "最接近常见的练习强度，默认选项。",
    },
    Difficulty.ADVANCED: {
        "name_zh": "Advanced 进阶",
        "questions": 13,
        "note": "信息密度更高，包含单选与信息定位。",
    },
}


def type_rows(recommended: dict[str, list[str]]) -> list[dict[str, object]]:
    """Every question type with its explanation, flagged when recommended."""
    rows: list[dict[str, object]] = []
    for question_type, guide in QUESTION_TYPE_GUIDE.items():
        rows.append(
            {
                "value": question_type.value,
                "label": question_type.value.replace("_", " "),
                "name_zh": guide["name_zh"],
                "what": guide["what"],
                "example": guide["example"],
                "grading": guide["grading"],
                "recommended_for": [
                    level for level, values in recommended.items() if question_type.value in values
                ],
            }
        )
    return rows


def difficulty_rows() -> list[dict[str, object]]:
    return [
        {
            "value": difficulty.value,
            "name_zh": DIFFICULTY_GUIDE[difficulty]["name_zh"],
            "questions": DIFFICULTY_GUIDE[difficulty]["questions"],
            "note": DIFFICULTY_GUIDE[difficulty]["note"],
        }
        for difficulty in Difficulty
    ]
