from __future__ import annotations

from app.models import UnitStatus
from app.pipeline.unit_runner import UnitRunner

from .conftest import (
    FailOnceQuestionValidator,
    PassValidator,
    review_failed,
    review_passed,
)


def make_runner(pipeline_app, fakes, **changes):
    config, repository, store, _unit = pipeline_app
    values = {
        "config": config,
        "repository": repository,
        "store": store,
        "author": fakes.author,
        "examiner": fakes.examiner,
        "source_text_loader": lambda unit: "原始中文材料",
        "passage_validator": PassValidator(),
        "question_validator": PassValidator(),
    }
    values.update(changes)
    return UnitRunner(**values)


def test_one_unit_happy_path_persists_package_and_stages(pipeline_app, fakes):
    _config, repository, store, unit = pipeline_app
    result = make_runner(pipeline_app, fakes).run(unit.id)

    assert result.status == UnitStatus.COMPLETED
    assert repository.get_unit(unit.id).status == UnitStatus.COMPLETED
    assert store._destination(f"packages/{unit.id}.json").is_file()
    assert fakes.author.brief_calls == 1
    assert fakes.author.write_calls == 1
    assert fakes.examiner.review_calls == 1
    assert fakes.examiner.assessment_calls == 1


def test_passage_issue_returns_only_to_author(pipeline_app, fakes):
    fakes.examiner.review_results = [
        review_failed("unsupported_specific_fact"),
        review_passed(),
    ]

    result = make_runner(pipeline_app, fakes).run("u1")

    assert fakes.author.revise_calls == 1
    assert fakes.examiner.repair_calls == 0
    assert result.status == UnitStatus.COMPLETED


def test_question_issue_repairs_only_failed_group(pipeline_app, fakes):
    validator = FailOnceQuestionValidator(group_id="true_false_not_given")

    result = make_runner(pipeline_app, fakes, question_validator=validator).run("u1")

    assert fakes.examiner.repaired_group_ids == [["true_false_not_given"]]
    assert fakes.author.revise_calls == 0
    assert result.status == UnitStatus.COMPLETED


def test_revision_limit_moves_unit_to_needs_review(pipeline_app, fakes):
    fakes.examiner.review_results = [review_failed("unsupported_specific_fact")]
    runner = make_runner(pipeline_app, fakes)

    result = runner.run("u1")

    assert fakes.author.revise_calls == 2
    assert result.status == UnitStatus.NEEDS_REVIEW


def test_restart_reuses_frozen_passage_without_recalling_author(pipeline_app, fakes):
    _config, repository, _store, _unit = pipeline_app

    def crash(stage):
        if stage == "author_passage":
            raise RuntimeError("simulated crash")

    first = make_runner(pipeline_app, fakes, after_stage=crash)
    try:
        first.run("u1")
    except RuntimeError as exc:
        assert "simulated" in str(exc)
    repository.recover_interrupted_units()

    second = make_runner(pipeline_app, fakes)
    result = second.run("u1")

    assert result.status == UnitStatus.COMPLETED
    assert fakes.author.write_calls == 1
    assert fakes.examiner.assessment_calls == 1
