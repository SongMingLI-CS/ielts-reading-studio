"""知识点汇总板块：把阅读题与情境小说的产物整理成"能看能读"的学习材料。

- `text`       ：分词、停用词、例句高亮
- `normalize`  ：词性/难度/条目类型（单词、固定搭配、短语动词）的统一口径
- `reading`    ：阅读侧词汇表与每道题的原文证据
- `novel`      ：小说侧术语库（含中英语境对照）
- `paraphrase` ：题干 ↔ 原文的同义替换对齐
- `digest`     ：合并、筛选、排序、分页
- `export`     ：Markdown 导出
"""

from .digest import (
    PAGE_SIZES,
    SORT_LABELS,
    SORTS,
    VIEW_LABELS,
    VIEWS,
    build_digest,
    collocation_rows,
    filter_terms,
    load_paraphrase_notes,
    merge_terms,
    paginate,
    sort_terms,
    source_kinds,
)
from .normalize import (
    KIND_LABELS,
    KIND_ORDER,
    LEVEL_LABELS,
    LEVELS,
    kind_of,
    resolve_level,
    resolve_pos,
)
from .paraphrase import align, question_note
from .text import (
    content_words,
    form_pattern,
    highlight,
    is_phrase,
    unique_content_words,
)

__all__ = [
    "KIND_LABELS",
    "KIND_ORDER",
    "LEVELS",
    "LEVEL_LABELS",
    "PAGE_SIZES",
    "SORTS",
    "SORT_LABELS",
    "VIEWS",
    "VIEW_LABELS",
    "align",
    "build_digest",
    "collocation_rows",
    "content_words",
    "filter_terms",
    "form_pattern",
    "highlight",
    "is_phrase",
    "kind_of",
    "load_paraphrase_notes",
    "merge_terms",
    "paginate",
    "question_note",
    "resolve_level",
    "resolve_pos",
    "sort_terms",
    "source_kinds",
    "unique_content_words",
]
