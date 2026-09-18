from __future__ import annotations

from copy import deepcopy

from app.models import Question, QuestionGroup, QuestionType
from app.validators.questions import validate_questions


def question(number: int, **changes):
    values = {
        "number": number,
        "prompt": f"Question {number}",
        "answer": "water",
        "acceptable_answers": [],
        "evidence_paragraph": "A",
        "evidence_quote": "water",
        "chinese_explanation": "A 段原文明确给出了答案及其原因。",
        "distractor_explanations": {},
    }
    values.update(changes)
    return Question(**values)


def group(question_type: QuestionType, questions: list[Question], **changes):
    values = {
        "type": question_type,
        "instructions": "Answer the questions.",
        "questions": questions,
    }
    values.update(changes)
    return QuestionGroup(**values)


def test_accepts_basic_grounded_question(valid_passage):
    report = validate_questions(
        valid_passage,
        [group(QuestionType.SHORT_ANSWER, [question(1)], word_limit=2)],
        expected_total=1,
    )
    assert report.passed


def test_flags_questions_that_repeat_within_the_same_unit(valid_passage):
    repeated = "Which two measures did the city council adopt to reduce household water use?"
    groups = [
        group(
            QuestionType.SHORT_ANSWER,
            [
                question(1, prompt=repeated, answer="water"),
                question(2, prompt=repeated, answer="water"),
            ],
            word_limit=2,
        )
    ]
    report = validate_questions(valid_passage, groups, expected_total=2)

    assert "question_duplicate_within_unit" in report.codes
    issue = next(
        item for item in report.issues if item.code == "question_duplicate_within_unit"
    )
    assert issue.affected_ids == ["1", "2"]


def test_flags_questions_that_repeat_an_existing_unit(valid_passage):
    from app.validators.similarity import QuestionStem

    prompt = "Which two measures did the city council adopt to reduce household water use?"
    groups = [
        group(
            QuestionType.SHORT_ANSWER,
            [question(1, prompt=prompt, answer="water")],
            word_limit=2,
        )
    ]
    report = validate_questions(
        valid_passage,
        groups,
        expected_total=1,
        existing_questions=[
            QuestionStem(
                unit_id="older-unit",
                number=7,
                group_type="short_answer",
                prompt=prompt,
                unit_title="An Older Passage",
            )
        ],
    )

    assert "question_duplicate_of_existing" in report.codes
    issue = next(item for item in report.issues if item.code == "question_duplicate_of_existing")
    assert "An Older Passage" in issue.message
    assert "7" in issue.message


def test_short_stems_do_not_trigger_the_duplicate_gate(valid_passage):
    groups = [
        group(
            QuestionType.TRUE_FALSE_NOT_GIVEN,
            [
                question(1, prompt="Judge claim 1.", answer="TRUE"),
                question(2, prompt="Judge claim 2.", answer="TRUE"),
            ],
        )
    ]
    report = validate_questions(valid_passage, groups, expected_total=2)

    assert "question_duplicate_within_unit" not in report.codes


def test_completion_answer_must_be_verbatim_and_within_limit(valid_passage):
    completion = group(
        QuestionType.SENTENCE_COMPLETION,
        [question(1, answer="words absent from passage")],
        word_limit=2,
    )
    report = validate_questions(valid_passage, [completion], expected_total=1)
    assert {"answer_not_in_passage", "answer_exceeds_word_limit"} <= set(report.codes)


def test_completion_answer_matching_is_case_insensitive(valid_passage):
    valid_passage.paragraphs[0].text += " Something Big appeared."
    completion = group(
        QuestionType.SUMMARY_COMPLETION,
        [question(1, answer="something big", evidence_quote="Something Big")],
        word_limit=2,
    )

    report = validate_questions(valid_passage, [completion], expected_total=1)

    assert "answer_not_in_passage" not in report.codes


def test_not_given_requires_nearest_context_and_specific_missing_reason(valid_passage):
    tfng = group(
        QuestionType.TRUE_FALSE_NOT_GIVEN,
        [
            question(
                1,
                answer="NOT GIVEN",
                evidence_quote="",
                chinese_explanation="文中没有说明。",
            )
        ],
    )
    report = validate_questions(valid_passage, [tfng], expected_total=1)
    assert "not_given_evidence_incomplete" in report.codes


def test_judgement_words_in_prompt_are_not_treated_as_answer_leaks(valid_passage):
    tfng = group(
        QuestionType.TRUE_FALSE_NOT_GIVEN,
        [
            question(
                1,
                prompt="The false entrance led to the real chamber.",
                answer="FALSE",
                chinese_explanation="原文明确说明方向相反。",
            )
        ],
    )

    report = validate_questions(valid_passage, [tfng], expected_total=1)

    assert "question_leaks_answer" not in report.codes


def test_evidence_must_be_exact_substring_of_named_paragraph(valid_passage):
    bad = group(
        QuestionType.SHORT_ANSWER,
        [question(1, evidence_paragraph="B", evidence_quote="not in the passage")],
        word_limit=2,
    )
    report = validate_questions(valid_passage, [bad], expected_total=1)
    assert "evidence_quote_not_found" in report.codes


def test_question_numbers_and_total_are_checked(valid_passage):
    bad = group(QuestionType.SHORT_ANSWER, [question(2)], word_limit=2)
    report = validate_questions(valid_passage, [bad], expected_total=2)
    assert {"question_count_mismatch", "question_numbers_invalid"} <= set(report.codes)


def test_matching_headings_requires_surplus_unique_options_and_answers(valid_passage):
    headings = group(
        QuestionType.MATCHING_HEADINGS,
        [question(1, answer="i"), question(2, answer="i")],
        options=["i", "ii"],
    )
    report = validate_questions(valid_passage, [headings], expected_total=2)
    assert {"heading_options_insufficient", "heading_answer_reused"} <= set(
        report.codes
    )


def test_multiple_choice_requires_unique_valid_answer_and_distractor_reasons(
    valid_passage,
):
    mc = group(
        QuestionType.MULTIPLE_CHOICE,
        [question(1, answer="D", distractor_explanations={"A": "wrong"})],
        options=["A", "B", "C", "D"],
    )
    report = validate_questions(valid_passage, [mc], expected_total=1)
    assert "distractor_explanations_missing" in report.codes


def test_duplicate_group_types_are_rejected(valid_passage):
    first = group(QuestionType.SHORT_ANSWER, [question(1)], word_limit=2)
    second = deepcopy(first)
    second.questions[0].number = 2
    report = validate_questions(valid_passage, [first, second], expected_total=2)
    assert "question_group_types_duplicate" in report.codes
