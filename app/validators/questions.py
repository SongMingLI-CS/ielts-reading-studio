from __future__ import annotations

import re
from collections import Counter

from app.models import Question, QuestionGroup, QuestionType, ReadingPassage

from .reports import ReportBuilder

COMPLETION_TYPES = {
    QuestionType.SENTENCE_COMPLETION,
    QuestionType.SUMMARY_COMPLETION,
    QuestionType.SHORT_ANSWER,
}
JUDGEMENT_TYPES = {
    QuestionType.TRUE_FALSE_NOT_GIVEN,
    QuestionType.YES_NO_NOT_GIVEN,
}


def validate_questions(
    passage: ReadingPassage,
    groups: list[QuestionGroup],
    expected_total: int,
):
    report = ReportBuilder("questions")
    paragraphs = {paragraph.label: paragraph.text for paragraph in passage.paragraphs}
    passage_text = "\n".join(paragraphs.values())
    questions = [question for group in groups for question in group.questions]

    if expected_total in {10, 12, 13} and len(groups) != 3:
        report.add(
            "question_group_count_mismatch",
            "Exactly three question groups are required.",
        )
    types = [group.type for group in groups]
    duplicates = [value.value for value, count in Counter(types).items() if count > 1]
    if duplicates:
        report.add(
            "question_group_types_duplicate",
            "Question group types must be distinct.",
            affected_ids=duplicates,
        )
    if len(questions) != expected_total:
        report.add(
            "question_count_mismatch",
            f"Assessment has {len(questions)} questions; expected {expected_total}.",
        )
    numbers = [question.number for question in questions]
    if numbers != list(range(1, len(questions) + 1)):
        report.add(
            "question_numbers_invalid",
            "Question numbers must be unique and continuous from 1.",
            affected_ids=[str(number) for number in numbers],
        )

    for group in groups:
        _validate_group(group, paragraphs, passage_text, report)

    evidence_checks = len(questions) - sum(
        issue.code in {"evidence_paragraph_invalid", "evidence_quote_not_found"}
        for issue in report.issues
    )
    return report.build(
        metrics={
            "question_count": len(questions),
            "question_group_count": len(groups),
            "evidence_checks_passed": max(0, evidence_checks),
        }
    )


def _validate_group(
    group: QuestionGroup,
    paragraphs: dict[str, str],
    passage_text: str,
    report: ReportBuilder,
) -> None:
    if group.type == QuestionType.MATCHING_HEADINGS:
        if len(group.options) <= len(group.questions):
            report.add(
                "heading_options_insufficient",
                "Matching Headings needs more headings than questions.",
                affected_ids=[group.type.value],
            )
        answers = [question.answer for question in group.questions]
        if len(answers) != len(set(answers)):
            report.add(
                "heading_answer_reused",
                "Matching Headings answers cannot reuse a heading.",
                affected_ids=[group.type.value],
            )
        if len(group.options) != len(set(group.options)):
            report.add(
                "heading_options_duplicate",
                "Matching Heading options must be unique.",
                affected_ids=[group.type.value],
            )

    for question in group.questions:
        _validate_question(group, question, paragraphs, passage_text, report)


def _validate_question(
    group: QuestionGroup,
    question: Question,
    paragraphs: dict[str, str],
    passage_text: str,
    report: ReportBuilder,
) -> None:
    affected = [str(question.number), group.type.value]
    paragraph = paragraphs.get(question.evidence_paragraph)
    if paragraph is None:
        report.add(
            "evidence_paragraph_invalid",
            "Evidence paragraph does not exist.",
            affected_ids=affected,
            question_number=question.number,
        )
    elif not question.evidence_quote or question.evidence_quote not in paragraph:
        report.add(
            "evidence_quote_not_found",
            "Evidence quote must be an exact substring of the named paragraph.",
            affected_ids=affected,
            question_number=question.number,
        )

    if group.type in COMPLETION_TYPES:
        accepted = [question.answer, *question.acceptable_answers]
        passage_folded = passage_text.casefold()
        if not any(
            answer and answer.casefold() in passage_folded for answer in accepted
        ):
            report.add(
                "answer_not_in_passage",
                "Completion and short-answer responses must occur verbatim in the passage.",
                affected_ids=affected,
                question_number=question.number,
            )
        if group.word_limit is None or group.word_limit < 1:
            report.add(
                "word_limit_missing",
                "Completion and short-answer groups require a positive word limit.",
                affected_ids=affected,
                question_number=question.number,
            )
        elif any(
            _word_count(answer) > group.word_limit for answer in accepted if answer
        ):
            report.add(
                "answer_exceeds_word_limit",
                "An answer exceeds the group word limit.",
                affected_ids=affected,
                question_number=question.number,
            )

    if group.type in JUDGEMENT_TYPES:
        if question.answer.upper().replace("/", " ") in {"NOT GIVEN", "NOTGIVEN"}:
            explanation = question.chinese_explanation.strip()
            specific_reason = len(explanation) >= 12 and any(
                marker in explanation
                for marker in (
                    "缺少",
                    "未提供",
                    "无法判断",
                    "没有交代",
                    "没有明确",
                    "并未说明",
                    "未说明",
                    "没有提到",
                    "未提到",
                )
            )
            if not question.evidence_quote or not specific_reason:
                report.add(
                    "not_given_evidence_incomplete",
                    "Not Given needs nearest context and a specific missing-information reason.",
                    affected_ids=affected,
                    question_number=question.number,
                )
        elif not question.chinese_explanation.strip():
            report.add(
                "judgement_reason_missing",
                "Judgement questions require a semantic reason.",
                affected_ids=affected,
                question_number=question.number,
            )

    if group.type == QuestionType.MULTIPLE_CHOICE:
        if len(group.options) < 3 or len(group.options) != len(set(group.options)):
            report.add(
                "multiple_choice_options_invalid",
                "Multiple Choice requires at least three unique options.",
                affected_ids=affected,
                question_number=question.number,
            )
        if group.options.count(question.answer) != 1:
            report.add(
                "multiple_choice_answer_not_unique",
                "Multiple Choice must have exactly one valid answer option.",
                affected_ids=affected,
                question_number=question.number,
            )
        distractors = [option for option in group.options if option != question.answer]
        if any(
            not question.distractor_explanations.get(option, "").strip()
            for option in distractors
        ):
            report.add(
                "distractor_explanations_missing",
                "Every incorrect option needs a distractor explanation.",
                affected_ids=affected,
                question_number=question.number,
            )

    answer_pattern = re.escape(question.answer.strip())
    if (
        group.type not in JUDGEMENT_TYPES
        and answer_pattern
        and re.search(
            rf"(?<!\w){answer_pattern}(?!\w)",
            question.prompt,
            flags=re.IGNORECASE,
        )
    ):
        report.add(
            "question_leaks_answer",
            "Question prompt directly contains its answer.",
            affected_ids=affected,
            question_number=question.number,
        )


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text, flags=re.UNICODE))
