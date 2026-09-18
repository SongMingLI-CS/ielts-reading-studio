"""知识点手册的 Markdown 导出：把当前筛选结果落成一份可粘贴、可打印的清单。"""

from __future__ import annotations

from typing import Any

MAX_ROWS = 400


def _source_text(row: dict[str, Any]) -> str:
    labels = [item["label"] for item in row["sources"]]
    return "、".join(labels) if labels else "—"


def _level_text(row: dict[str, Any]) -> str:
    marks = "" if row.get("level_source") == "cefr" else "（按词形推断）"
    return f"{row['level']}{marks}"


def term_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows[:MAX_ROWS]:
        head = f"### {row['headword']}"
        meta = [row.get("pos_label") or "", _level_text(row)]
        if row.get("phonetic"):
            meta.insert(0, row["phonetic"])
        lines.append(head)
        lines.append("")
        lines.append(f"- 词性与难度：{' · '.join(item for item in meta if item)}")
        if row.get("meaning"):
            lines.append(f"- 释义：{row['meaning']}")
        if row["collocations"]:
            lines.append(f"- 固定搭配：{'；'.join(row['collocations'])}")
        if row.get("example"):
            lines.append(f"- 例句：{row['example']}")
        for context in row.get("contexts", []):
            lines.append(f"- 中英语境：{context['zh']} → {context['en']}")
        counts = row["source_counts"]
        lines.append(
            f"- 出现：阅读 {counts['reading']} 篇 · 小说 {counts['novel']} 次（{_source_text(row)}）"
        )
        lines.append("")
    return lines


def paraphrase_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows[:MAX_ROWS]:
        lines.append(f"### {row['passage_title']} · 第 {row['number']} 题")
        lines.append("")
        lines.append(f"- 题干：{row['prompt']}")
        lines.append(f"- 原文（{row['evidence_label']}）：{row['evidence_quote']}")
        lines.append(f"- 答案：{row['answer']}")
        if row["form_pairs"]:
            pairs = "；".join(
                f"{item['prompt']} ≈ {item['quote']}" for item in row["form_pairs"]
            )
            lines.append(f"- 词形变化：{pairs}")
        if row["prompt_markers"]:
            lines.append(f"- 题干替换措辞：{'、'.join(row['prompt_markers'])}")
        if row["quote_markers"]:
            lines.append(f"- 原文对方措辞：{'、'.join(row['quote_markers'])}")
        lines.append(f"- 解析：{row['chinese_explanation']}")
        lines.append("")
    return lines


def to_markdown(digest: dict[str, Any]) -> str:
    """按当前视图生成 Markdown。"""
    view = digest["view"]
    rows = digest["rows"]
    stats = digest["stats"]
    header = [
        "# 知识点汇总",
        "",
        f"- 视图：{digest['view_label']}",
        f"- 本页 {len(rows)} 条 / 共 {digest['pagination']['total']} 条",
        f"- 全部统计：词条 {stats['terms']} · 固定搭配 {stats['collocations']} · 同义替换 {stats['paraphrase']}",
        f"- 来源：阅读练习 {stats['reading_units']} 篇 · 情境小说 {stats['novel_chapters']} 章",
        "",
    ]
    if view == "paraphrase":
        body = paraphrase_lines(rows)
    elif view == "collocations":
        body = []
        for row in rows[:MAX_ROWS]:
            body.append(f"### {row['headword']}")
            body.append("")
            body.append(
                f"- 所属词：{row['word']}"
                + (f"（{row['meaning']}）" if row.get("meaning") else "")
            )
            if row.get("example"):
                body.append(f"- 例句：{row['example']}")
            body.append(f"- 难度：{_level_text(row)}")
            body.append(f"- 出处：{_source_text(row)}")
            body.append("")
    else:
        body = term_lines(rows)
    return "\n".join([*header, *body]).rstrip() + "\n"
