"""词汇板块：把练习里出现的词整理成"能背"的形态。

- `collection`：从已完成的 Package 汇总词条（跨篇合并、带上掌握与复习状态）
- `classify`  ：词性/复现频率/来源/难度分类，以及同根词与联想
- `srs`       ：Leitner 盒子的复习排程（间隔递增、答错退回第一盒）
"""

from .classify import (
    POS_GROUPS,
    classify_rows,
    difficulty_hint,
    form_families,
    infer_pos,
    pos_group,
    related_words,
)
from .collection import as_aware, collect_vocabulary, due_rows, study_rows, tracked_rows
from .srs import BOX_INTERVALS, due_date, interval_days, next_box, schedule

__all__ = [
    "BOX_INTERVALS",
    "POS_GROUPS",
    "as_aware",
    "classify_rows",
    "collect_vocabulary",
    "difficulty_hint",
    "due_date",
    "due_rows",
    "form_families",
    "infer_pos",
    "interval_days",
    "next_box",
    "pos_group",
    "related_words",
    "schedule",
    "study_rows",
    "tracked_rows",
]
