from __future__ import annotations

import pytest

from app.models import QualityReport, UnitStatus


@pytest.fixture
def sampled_job(web_service, completed_unit):
    """A finished job with one completed unit in the review queue."""
    web_service.repository.create_job(
        "job-sample",
        completed_unit.corpus_id,
        "completed",
        {"unit_ids": [completed_unit.id]},
    )
    web_service.repository.assign_units_to_job([completed_unit.id], "job-sample")
    rows = web_service.sample_job_units("job-sample", size=1)
    return rows


def test_review_queue_explains_itself_when_empty(client):
    page = client.get("/review")

    assert page.status_code == 200
    assert "抽样审阅" in page.text
    assert "队列是空的" in page.text
    assert "再抽一批样本" in page.text


def test_sampling_a_finished_job_fills_the_queue(client, web_service, completed_unit, sampled_job):
    assert [row["unit_id"] for row in sampled_job] == [completed_unit.id]

    page = client.get("/review")
    assert completed_unit.package.passage.title in page.text
    assert f"/practice/{completed_unit.id}/analysis" in page.text
    assert "待审样本（1）" in page.text


def test_sampling_is_stable_and_keeps_existing_decisions(client, web_service, completed_unit, sampled_job):
    again = web_service.sample_job_units("job-sample", size=1)
    assert again == []  # 已经在队列里且未决定，不重复生成

    web_service.decide_sample(completed_unit.id, approved=True, note="看起来没问题")
    rerun = web_service.sample_job_units("job-sample", size=1)

    assert rerun == []  # 已人工审过的不再抽出来覆盖


def test_decide_endpoint_records_pass_and_rework(client, web_service, completed_unit, sampled_job):
    approved = client.post(
        f"/review/{completed_unit.id}/decide",
        data={"decision": "pass", "note": "第 7 题证据句偏弱但可接受"},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert "decided=passed" in approved.headers["location"]
    sample = web_service.repository.get_review_sample(completed_unit.id)
    assert sample["status"] == "passed"
    assert "第 7 题" in sample["payload"]

    rework = client.post(
        f"/review/{completed_unit.id}/decide",
        data={"decision": "rework", "note": "第 7 题必须重做"},
        follow_redirects=False,
    )
    assert rework.status_code == 303
    assert "decided=failed" in rework.headers["location"]
    assert web_service.repository.get_review_sample(completed_unit.id)["status"] == "failed"


def test_decide_rejects_unknown_unit_and_bad_decision(client, completed_unit, sampled_job):
    assert client.post("/review/missing-unit/decide", data={"decision": "pass"}).status_code == 404
    assert (
        client.post(
            f"/review/{completed_unit.id}/decide", data={"decision": "maybe"}
        ).status_code
        == 422
    )


def test_sample_endpoint_needs_a_job(client):
    response = client.post("/review/sample")

    assert response.status_code == 409
    assert "sample" in response.text or "job" in response.text.lower()


def test_sample_endpoint_reports_jobs_without_completed_units(client, web_service, sample_txt):
    import_response = web_service.import_source(sample_txt)
    web_service.repository.create_job(
        "job-idle", import_response.corpus.id, "queued", {"unit_ids": []}
    )

    response = client.post("/review/sample")

    assert response.status_code == 409


def test_similarity_page_lists_duplicate_questions(client, web_service, completed_unit, tmp_path):
    duplicate = completed_unit.package.model_copy(deep=True)
    unit = duplicate.unit.model_copy(
        update={"id": "duplicate-unit", "ordinal": 9, "status": UnitStatus.COMPLETED}
    )
    prompt = "Which two measures did the city council adopt to reduce household water use?"
    for group in duplicate.question_groups:
        for question in group.questions:
            question.prompt = prompt
    for group in completed_unit.package.question_groups:
        for question in group.questions:
            question.prompt = prompt
    web_service.store.write_package(
        completed_unit.id,
        completed_unit.package.model_copy(
            update={"quality_report": QualityReport(passed=True)}
        ),
    )
    web_service.repository.add_unit(unit)
    web_service.store.write_package(
        unit.id, duplicate.model_copy(update={"unit": unit, "quality_report": QualityReport(passed=True)})
    )

    page = client.get("/review/similarity")

    assert page.status_code == 200
    assert "相似题目" in page.text
    assert "100% 相似" in page.text
    assert "/practice/duplicate-unit/analysis" in page.text


def test_similarity_page_accepts_a_threshold(client, completed_unit):
    page = client.get("/review/similarity?threshold=0.95")

    assert page.status_code == 200
    assert "95%" in page.text
    assert client.get("/review/similarity?threshold=5").status_code == 422
