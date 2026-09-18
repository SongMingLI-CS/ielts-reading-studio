from __future__ import annotations

import re
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
                "pronunciation": "ˈsiːzənəl",
                "part_of_speech": "adj.",
                "chinese_meaning": "季节性的",
                "collocations": ["seasonal supplies"],
                "example": "Seasonal supplies can decline.",
            },
            {
                "word": "supplies",
                "pronunciation": "səˈplaɪz",
                "part_of_speech": "n.",
                "chinese_meaning": "供给；补给",
                "collocations": ["water supplies"],
                "example": "Communities manage water carefully because seasonal supplies can decline.",
            },
            {
                "word": "decline",
                "pronunciation": "dɪˈklaɪn",
                "part_of_speech": "v.",
                "chinese_meaning": "下降；减少",
                "collocations": ["supplies decline"],
                "example": "Seasonal supplies can decline.",
            },
            {
                "word": "carefully",
                "pronunciation": "ˈkeəfəli",
                "part_of_speech": "adv.",
                "chinese_meaning": "小心地；谨慎地",
                "collocations": ["manage carefully"],
                "example": "Communities manage water carefully.",
            },
            {
                "word": "communities",
                "pronunciation": "kəˈmjuːnətiz",
                "part_of_speech": "n.",
                "chinese_meaning": "社区；群体",
                "collocations": ["local communities"],
                "example": "Communities manage water carefully.",
            },
            {
                "word": "manage",
                "pronunciation": "ˈmænɪdʒ",
                "part_of_speech": "v.",
                "chinese_meaning": "管理；设法做到",
                "collocations": ["manage water"],
                "example": "Communities manage water carefully.",
            },
            {
                "word": "water",
                "pronunciation": "ˈwɔːtə",
                "part_of_speech": "n.",
                "chinese_meaning": "水",
                "collocations": ["water supplies"],
                "example": "Communities manage water carefully.",
            },
            {
                "word": "seasonal supplies",
                "pronunciation": "ˈsiːzənəl səˈplaɪz",
                "part_of_speech": "n.",
                "chinese_meaning": "季节性供给",
                "collocations": ["seasonal supplies decline"],
                "example": "Seasonal supplies can decline.",
            },
        ],
        "source_coverage": {"f1": ["A"], "c1": ["B"]},
        "author_revision": 0,
    }


def valid_review_payload() -> dict:
    return {"passed": True, "issues": [], "requested_changes": []}


def _topic_token(request: ModelRequest) -> str:
    """给假 Provider 一个"这篇讲什么"的词，让不同篇目的题干不再一模一样。

    真实模型不会给两篇文章出完全相同的题；跨篇去重上线后，固定题干会被质检
    正确拦下，所以夹具必须带上篇目特有的词。
    """
    match = re.search(r'"title"\s*:\s*"([^"]+)"', request.user)
    if match:
        words = re.findall(r"[A-Za-z]{3,}", match.group(1))
        if words:
            return words[0].casefold()
    return f"passage{len(request.user) % 97}"


def valid_assessment_payload(
    difficulty: Difficulty = Difficulty.STANDARD, topic: str = ""
) -> dict:
    evidence = "Communities manage water carefully"
    if difficulty == Difficulty.FOUNDATION:
        payload = _foundation_assessment(evidence)
    elif difficulty == Difficulty.ADVANCED:
        payload = _advanced_assessment(evidence)
    else:
        payload = _standard_assessment(evidence)
    if topic:
        # 连成一个 token（topic-focus-xxx），既让不同篇目彼此不同，又不会
        # 意外撞上答案导致 question_leaks_answer。
        for group in payload["question_groups"]:
            for question in group["questions"]:
                question["prompt"] = f"{question['prompt']} Topic-focus-{topic}."
    return payload


def _standard_assessment(evidence: str) -> dict:
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
            payload = valid_assessment_payload(self.difficulty, _topic_token(request))
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
