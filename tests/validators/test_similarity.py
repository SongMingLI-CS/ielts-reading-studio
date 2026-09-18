from __future__ import annotations

from app.models import QuestionType
from app.validators.similarity import (
    QuestionIndex,
    QuestionStem,
    content_tokens,
    is_duplicate,
    similarity,
    stems_from_groups,
)


def test_similarity_ranks_paraphrase_above_unrelated():
    paraphrase = similarity(
        "What does the writer suggest about conserving water in cities?",
        "What is suggested about conserving water in cities?",
    )
    unrelated = similarity(
        "What does the writer suggest about conserving water in cities?",
        "How many visitors arrived at the museum in 1890?",
    )

    assert paraphrase > unrelated
    assert unrelated == 0.0


def test_identical_prompts_score_one():
    prompt = "The museum was founded in 1890 by a local merchant."

    assert similarity(prompt, prompt) == 1.0


def test_content_tokens_drop_function_words_and_keep_numbers():
    tokens = content_tokens("According to the passage, the writer mentions 1890 water.")

    assert "water" in tokens and "1890" in tokens
    assert "the" not in tokens and "passage" not in tokens


def test_short_stems_differing_only_by_number_are_not_duplicates():
    duplicate, score = is_duplicate(
        "Judge claim 5.", "Judge claim 6.", threshold=0.8
    )

    assert duplicate is False
    assert score < 0.72  # 只差编号，共有实词不够，不能据此判重


def test_terse_matching_stems_are_not_duplicates():
    duplicate, _ = is_duplicate(
        "Choose a heading for paragraph A.",
        "Choose a heading for paragraph B.",
        threshold=0.8,
    )

    assert duplicate is False


def test_substantial_duplicates_are_flagged():
    duplicate, score = is_duplicate(
        "Which two measures did the city council adopt to reduce household water use?",
        "Which two measures did the city council adopt to reduce household water usage?",
        threshold=0.72,
    )

    assert duplicate is True
    assert score >= 0.72


def test_stems_from_groups_carries_unit_context():
    from tests.validators.test_questions import group, question

    stems = stems_from_groups(
        "unit-1",
        [group(QuestionType.SHORT_ANSWER, [question(1)], word_limit=2)],
        ordinal=3,
        title="Managing Water",
    )

    assert len(stems) == 1
    assert stems[0].unit_id == "unit-1"
    assert stems[0].unit_ordinal == 3
    assert stems[0].unit_title == "Managing Water"
    assert stems[0].as_dict()["group_type"] == "short_answer"


def stem(unit_id: str, number: int, prompt: str) -> QuestionStem:
    return QuestionStem(
        unit_id=unit_id, number=number, group_type="short_answer", prompt=prompt
    )


def test_index_matches_across_units_and_skips_its_own_unit():
    index = QuestionIndex(threshold=0.8)
    prompt = "Which two measures did the council adopt to reduce household water use?"
    index.add([stem("unit-1", 3, prompt), stem("unit-2", 1, prompt)])

    across = index.match(stem("unit-3", 5, prompt))
    skipping_own = index.match(stem("unit-2", 5, prompt), skip_unit_id="unit-2")

    assert [match.right.unit_id for match in across] == ["unit-1", "unit-2"]
    assert [match.right.unit_id for match in skipping_own] == ["unit-1"]


def test_index_duplicate_pairs_reports_each_question_once():
    index = QuestionIndex(threshold=0.8)
    prompt = "Which two measures did the council adopt to reduce household water use?"
    index.add(
        [
            stem("unit-1", 1, prompt),
            stem("unit-2", 2, prompt),
            stem("unit-3", 3, prompt),
        ]
    )

    pairs = index.duplicate_pairs()

    assert pairs
    assert len({pair.key for pair in pairs}) == len(pairs)
    assert all(pair.left.unit_id != pair.right.unit_id for pair in pairs)


def test_stems_excluding_returns_a_snapshot_without_that_unit():
    index = QuestionIndex()
    index.add([stem("unit-1", 1, "First question about water."), stem("unit-2", 1, "Second.")])
    index.add([stem("unit-3", 1, "Third.")])

    remaining = index.stems_excluding("unit-1")

    assert {item.unit_id for item in remaining} == {"unit-2", "unit-3"}
    assert len(index) == 3
