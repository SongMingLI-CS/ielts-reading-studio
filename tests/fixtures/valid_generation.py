from __future__ import annotations

from threading import Lock

from app.agents.base import ModelRequest, ModelResult
from app.models import Difficulty, QuestionType


def valid_brief_payload() -> dict:
    return {
        "core_facts": [{"id": "f1", "text": "Water is limited.", "source_ids": ["source"]}],
        "core_claims": [{"id": "c1", "text": "Careful management matters.", "source_ids": ["source"]}],
        "causal_links": [],
        "uncertainties": [],
        "prohibited_inventions": ["specific new facts"],
        "suggested_structure": ["context", "management"],
    }


def valid_passage_payload(difficulty: Difficulty = Difficulty.STANDARD) -> dict:
    sentence = "Communities manage water carefully because seasonal supplies can decline. "
    paragraphs = [
        {"label": label, "text": sentence * 15, "source_ids": ["f1", "c1"]}
        for label in "ABCDEF"
    ]
    return {
        "title": "Managing Water Carefully",
        "difficulty": difficulty.value,
        "word_count": 810,
        "paragraphs": paragraphs,
        "vocabulary": [
            {
                "word": "seasonal",
                "pronunciation": "/ˈsiːzənəl/",
                "part_of_speech": "adjective",
                "chinese_meaning": "季节性的",
                "collocations": ["seasonal supplies"],
                "example": "Seasonal supplies can decline.",
            }
        ],
        "source_coverage": {"f1": ["A"], "c1": ["B"]},
        "author_revision": 0,
    }


def valid_review_payload() -> dict:
    return {"passed": True, "issues": [], "requested_changes": []}


def valid_assessment_payload(difficulty: Difficulty = Difficulty.STANDARD) -> dict:
    evidence = "Communities manage water carefully"
    if difficulty == Difficulty.FOUNDATION:
        return _foundation_assessment(evidence)
    if difficulty == Difficulty.ADVANCED:
        return _advanced_assessment(evidence)
    groups: list[dict] = []
    heading_questions = []
    for number, (label, answer) in enumerate(zip("ABCD", ["i", "ii", "iii", "iv"]), start=1):
        heading_questions.append(_question(number, f"Choose a heading for paragraph {label}.", answer, label, evidence))
    groups.append(
        {
            "type": QuestionType.MATCHING_HEADINGS.value,
            "instructions": "Choose the correct heading.",
            "word_limit": None,
            "options": ["i", "ii", "iii", "iv", "v"],
            "questions": heading_questions,
        }
    )
    judgement_answers = ["TRUE", "FALSE", "NOT GIVEN", "TRUE"]
    judgement_questions = []
    for offset, answer in enumerate(judgement_answers, start=5):
        explanation = (
            "原文没有交代比较对象，因此缺少作出判断所需的信息。"
            if answer == "NOT GIVEN"
            else "原文证据足以支持这一语义判断。"
        )
        judgement_questions.append(
            _question(offset, f"Judge statement {offset}.", answer, "A", evidence, explanation)
        )
    groups.append(
        {
            "type": QuestionType.TRUE_FALSE_NOT_GIVEN.value,
            "instructions": "Choose TRUE, FALSE or NOT GIVEN.",
            "word_limit": None,
            "options": [],
            "questions": judgement_questions,
        }
    )
    completion_questions = [
        _question(number, f"Complete statement {number}.", "water", "A", evidence)
        for number in range(9, 13)
    ]
    groups.append(
        {
            "type": QuestionType.SUMMARY_COMPLETION.value,
            "instructions": "Complete the summary.",
            "word_limit": 2,
            "options": [],
            "questions": completion_questions,
        }
    )
    return {"question_groups": groups}


def _foundation_assessment(evidence: str) -> dict:
    heading_questions = [
        _question(number, f"Choose a heading for paragraph {label}.", answer, label, evidence)
        for number, (label, answer) in enumerate(zip("ABC", ["i", "ii", "iii"]), start=1)
    ]
    judgement_questions = [
        _question(number, f"Judge statement {number}.", "TRUE", "A", evidence)
        for number in range(4, 7)
    ]
    completion_questions = [
        _question(number, f"Complete statement {number}.", "water", "A", evidence)
        for number in range(7, 11)
    ]
    return {
        "question_groups": [
            {
                "type": QuestionType.MATCHING_HEADINGS.value,
                "instructions": "Choose the correct heading.",
                "word_limit": None,
                "options": ["i", "ii", "iii", "iv"],
                "questions": heading_questions,
            },
            {
                "type": QuestionType.TRUE_FALSE_NOT_GIVEN.value,
                "instructions": "Choose TRUE, FALSE or NOT GIVEN.",
                "word_limit": None,
                "options": [],
                "questions": judgement_questions,
            },
            {
                "type": QuestionType.SENTENCE_COMPLETION.value,
                "instructions": "Complete each sentence.",
                "word_limit": 2,
                "options": [],
                "questions": completion_questions,
            },
        ]
    }


def _advanced_assessment(evidence: str) -> dict:
    information = [
        _question(number, f"Locate information {number}.", label, label, evidence)
        for number, label in enumerate("ABCD", start=1)
    ]
    judgements = [
        _question(number, f"Judge claim {number}.", "YES", "A", evidence)
        for number in range(5, 9)
    ]
    multiple_choice = []
    for number in range(9, 14):
        item = _question(number, f"Select the best option for item {number}.", "A", "A", evidence)
        item["distractor_explanations"] = {"B": "Not supported.", "C": "Too broad.", "D": "Contradicted."}
        multiple_choice.append(item)
    return {
        "question_groups": [
            {
                "type": QuestionType.MATCHING_INFORMATION.value,
                "instructions": "Match information to paragraphs.",
                "word_limit": None,
                "options": ["A", "B", "C", "D", "E", "F"],
                "questions": information,
            },
            {
                "type": QuestionType.YES_NO_NOT_GIVEN.value,
                "instructions": "Choose YES, NO or NOT GIVEN.",
                "word_limit": None,
                "options": [],
                "questions": judgements,
            },
            {
                "type": QuestionType.MULTIPLE_CHOICE.value,
                "instructions": "Choose one option.",
                "word_limit": None,
                "options": ["A", "B", "C", "D"],
                "questions": multiple_choice,
            },
        ]
    }


def _question(
    number: int,
    prompt: str,
    answer: str,
    paragraph: str,
    evidence: str,
    explanation: str = "原文证据明确支持该答案。",
) -> dict:
    return {
        "number": number,
        "prompt": prompt,
        "answer": answer,
        "acceptable_answers": [],
        "evidence_paragraph": paragraph,
        "evidence_quote": evidence,
        "chinese_explanation": explanation,
        "distractor_explanations": {},
    }


class DeterministicProvider:
    """Thread-safe fake provider that never opens a network connection."""

    def __init__(self, difficulty: Difficulty = Difficulty.STANDARD) -> None:
        self.requests: list[ModelRequest] = []
        self._lock = Lock()
        self.difficulty = difficulty

    def complete_json(self, request: ModelRequest) -> ModelResult:
        with self._lock:
            self.requests.append(request)
        if request.stage == "author_brief":
            payload = valid_brief_payload()
        elif request.stage.startswith("author_passage"):
            payload = valid_passage_payload(self.difficulty)
        elif request.stage.startswith("examiner_passage_review"):
            payload = valid_review_payload()
        elif request.stage.startswith("examiner_assessment"):
            payload = valid_assessment_payload(self.difficulty)
        else:
            raise AssertionError(f"Unexpected stage: {request.stage}")
        return ModelResult(
            payload=payload,
            raw_text="{}",
            input_tokens=100,
            output_tokens=50,
            elapsed_ms=5,
            model=request.model,
            response_id=f"fake-{len(self.requests)}",
            finish_reason="stop",
        )
