"""读取阅读练习产出的知识点：词汇表条目 + 每道题的同义替换线索。

词汇条目复用 ``app.vocabulary.collection.collect_vocabulary``（跨篇合并、带收藏状态），
这里的职责只是把它翻译成知识点口径，并补上出处链接。
"""

from __future__ import annotations

from typing import Any

from app.models import QuestionType, UnitStatus
from app.vocabulary import collect_vocabulary

from .normalize import kind_of, resolve_level, resolve_pos
from .paraphrase import question_note

DIFFICULTY_LABELS = {
    "foundation": "基础",
    "standard": "标准",
    "advanced": "进阶",
}


def _difficulty_label(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "")
    return DIFFICULTY_LABELS.get(text, text.title() or "未标注")


def load_reading_terms(service: Any) -> dict[str, Any]:
    """阅读侧的词汇/搭配条目（来源：已校验的 Passage.vocabulary）。"""
    rows = collect_vocabulary(service)
    unit_ids = {unit_id for row in rows for unit_id in row["unit_ids"]}
    terms: list[dict[str, Any]] = []
    for row in rows:
        word = row["word"]
        kind = kind_of(word, row["part_of_speech"])
        sources = [
            {
                "kind": "reading",
                "label": f"阅读练习 · {title}",
                "href": f"/practice/{unit_id}/compare",
            }
            for title, unit_id in zip(row["passages"], row["unit_ids"])
        ]
        terms.append(
            {
                "key": row["key"],
                "headword": word,
                "kind": kind,
                "phonetic": (row["pronunciation"] or "").strip(),
                "meaning": (row["chinese_meaning"] or "").strip(),
                "example": (row["example"] or "").strip(),
                "collocations": list(row["collocations"]),
                "occurrences": int(row.get("passage_count") or 0),
                "category": "",
                "contexts": [],
                "sources": sources,
                "status": row.get("status", ""),
                **resolve_level(word, None),
                **resolve_pos(word, row["part_of_speech"]),
            }
        )
    return {"terms": terms, "unit_count": len(unit_ids)}


def _heading_text(options: list[str], answer: str) -> str:
    """匹配标题题：答案是一串罗马数字，真正的标题在选项里。

    返回标题正文（去掉 "vi. " 这类前缀）；找不到就返回空串，让页面退回用题干。
    """
    cleaned = (answer or "").strip().rstrip(".")
    if not cleaned:
        return ""
    for option in options or []:
        text = (option or "").strip()
        prefix, separator, rest = text.partition(".")
        if separator and prefix.strip().casefold() == cleaned.casefold() and rest.strip():
            return rest.strip()
    return ""


def load_question_notes(service: Any) -> list[dict[str, Any]]:
    """每道已生成题目的"题干 ↔ 原文"对照（同义替换线索）。"""
    notes: list[dict[str, Any]] = []
    for corpus in service.repository.list_corpora():
        for unit in service.repository.list_units(corpus.id):
            if unit.status != UnitStatus.COMPLETED:
                continue
            try:
                package = service.load_package(unit.id)
            except (FileNotFoundError, ValueError):
                continue
            title = package.passage.title
            difficulty = _difficulty_label(package.passage.difficulty)
            for group in package.question_groups:
                for question in group.questions:
                    prompt = question.prompt
                    prompt_label = "题干"
                    answer_label = "答案"
                    prompt_raw = question.prompt
                    hint = ""
                    if group.type == QuestionType.MATCHING_HEADINGS:
                        heading = _heading_text(group.options, question.answer)
                        if heading:
                            # 「概括性标题 ↔ 段落里的具体描述」是这一题型真正的考点
                            prompt = heading
                            prompt_label = f"标题（选项 {question.answer.strip()}）"
                            answer_label = "答案选项"
                            hint = "标题是概括说法，原文是具体描述：定位靠意思对应，不是原词"
                    notes.append(
                        question_note(
                            unit_id=unit.id,
                            passage_title=title,
                            difficulty=difficulty,
                            number=question.number,
                            prompt=prompt,
                            answer=question.answer,
                            evidence_label=question.evidence_paragraph,
                            evidence_quote=question.evidence_quote,
                            chinese_explanation=question.chinese_explanation,
                            question_type=str(getattr(group.type, "value", group.type)),
                            prompt_label=prompt_label,
                            answer_label=answer_label,
                            prompt_raw=prompt_raw,
                            hint_override=hint,
                        )
                    )
    notes.sort(key=lambda item: (item["passage_title"], item["number"]))
    return notes
