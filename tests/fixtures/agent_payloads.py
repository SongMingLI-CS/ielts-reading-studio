from __future__ import annotations

from app.models import Difficulty, QuestionType


def brief_payload() -> dict:
    return {
        "core_facts": [{"id": "f1", "text": "Water is limited.", "source_ids": ["s1"]}],
        "core_claims": [{"id": "c1", "text": "Careful use matters.", "source_ids": ["s1"]}],
        "causal_links": [],
        "uncertainties": [],
        "prohibited_inventions": ["new statistics"],
        "suggested_structure": ["problem", "response"],
    }


def passage_payload(*, revision: int = 0) -> dict:
    paragraphs = [
        {"label": label, "text": f"Paragraph {label} discusses water supply and careful use.", "source_ids": ["f1", "c1"]}
        for label in "ABCDEF"
    ]
    return {
        "title": "Managing Water",
        "difficulty": Difficulty.STANDARD.value,
        "word_count": 720,
        "paragraphs": paragraphs,
        "vocabulary": [],
        "source_coverage": {"f1": ["A"], "c1": ["B"]},
        "author_revision": revision,
    }


def review_payload(*, passed: bool = True) -> dict:
    return {
        "passed": passed,
        "issues": [] if passed else [
            {
                "code": "unsupported_specific_fact",
                "message": "Remove the unsupported date.",
                "affected_ids": ["A"],
            }
        ],
        "requested_changes": [] if passed else ["Remove the unsupported date."],
    }


def assessment_payload(*, evidence_quote: str | None = "water supply") -> dict:
    types = [
        QuestionType.MATCHING_HEADINGS,
        QuestionType.TRUE_FALSE_NOT_GIVEN,
        QuestionType.SUMMARY_COMPLETION,
    ]
    groups = []
    number = 1
    for question_type in types:
        questions = []
        for _ in range(4):
            questions.append(
                {
                    "number": number,
                    "prompt": f"Question {number}",
                    "answer": "water supply",
                    "acceptable_answers": [],
                    "evidence_paragraph": "A",
                    "evidence_quote": evidence_quote,
                    "chinese_explanation": "原文给出了答案。",
                    "distractor_explanations": {},
                }
            )
            number += 1
        groups.append(
            {
                "type": question_type.value,
                "instructions": "Answer the questions.",
                "word_limit": 2 if question_type == QuestionType.SUMMARY_COMPLETION else None,
                "options": [],
                "questions": questions,
            }
        )
    return {"question_groups": groups}
