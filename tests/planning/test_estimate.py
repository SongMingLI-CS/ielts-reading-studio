from __future__ import annotations

from app.models import Difficulty
from app.planning.estimate import estimate_run
from app.planning.units import default_question_types, plan_units

DEFAULT_TYPES = default_question_types(Difficulty.STANDARD)


def plan(config, chapter, sizes=(500, 500, 500, 500)):
    chapters = [
        chapter(f"c{index}", "甲" * size, ordinal=index) for index, size in enumerate(sizes, start=1)
    ]
    return plan_units(chapters, config, Difficulty.STANDARD, DEFAULT_TYPES, corpus_id="corpus-1")


def test_estimate_counts_both_agents_and_permitted_repairs(config, chapter):
    units = plan(config, chapter)

    estimate = estimate_run(units, author_revisions=2, examiner_revisions=2)

    # One brief, one passage, one passage review and one assessment per unit.
    assert estimate.minimum_requests == len(units) * 4
    # Two author revisions each need a rewrite plus a fresh review, and two
    # examiner revisions each need one assessment repair.
    assert estimate.maximum_requests == len(units) * 10
    assert estimate.minimum_tokens < estimate.maximum_tokens


def test_estimate_without_revisions_is_the_base_cost(config, chapter):
    units = plan(config, chapter)

    estimate = estimate_run(units, author_revisions=0, examiner_revisions=0)

    assert estimate.minimum_requests == len(units) * 4
    assert estimate.maximum_requests == len(units) * 4
    assert estimate.per_unit_minimum_requests == 4
    assert estimate.per_unit_maximum_requests == 4


def test_estimate_scales_with_source_length(config, chapter):
    small = plan(config, chapter, sizes=(1000,))
    large = plan(config, chapter, sizes=(8000, 8000))

    assert estimate_run(small).maximum_tokens < estimate_run(large).maximum_tokens
    assert estimate_run(small).minimum_tokens < estimate_run(large).minimum_tokens


def test_estimate_reports_bounds_without_inventing_currency(config, chapter):
    estimate = estimate_run(plan(config, chapter))

    assert estimate.pricing_available is False
    assert estimate.minimum_tokens > 0
    assert estimate.maximum_tokens > estimate.minimum_tokens
    serialized = estimate.model_dump_json()
    assert "$" not in serialized
    assert "¥" not in serialized


def test_estimate_counts_limited_source_units(config, chapter):
    units = plan(config, chapter, sizes=(100, 100))

    estimate = estimate_run(units)

    assert estimate.limited_source_units == len(units)
    assert estimate.unit_count == len(units)


def test_estimate_of_no_units_is_zero(config):
    estimate = estimate_run([])

    assert estimate.unit_count == 0
    assert estimate.minimum_requests == 0
    assert estimate.maximum_requests == 0
    assert estimate.minimum_tokens == 0
    assert estimate.maximum_tokens == 0
