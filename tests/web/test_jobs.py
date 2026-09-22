from pydantic import SecretStr

from app.models import UnitStatus


def test_configuration_page_explains_types_and_the_gate(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    response = client.get(f"/corpora/{manifest.corpus.id}/configure")
    assert response.status_code == 200
    # the estimate keeps being shown
    assert "Token" in response.text
    assert "请求次数估算" in response.text
    # the gate is explained, not just enforced
    assert "生成样篇" in response.text
    assert "人工批准" in response.text
    assert "批量生成已锁定" in response.text
    # question types come with plain-language explanations
    assert "小标题匹配" in response.text
    assert "事实判断（TRUE / FALSE / NOT GIVEN）" in response.text
    assert "摘要填空" in response.text
    assert "例：" in response.text
    assert "判定：" in response.text
    # difficulty presets are visible
    assert "Foundation 基础" in response.text
    assert "10 题" in response.text
    assert "Advanced 进阶" in response.text
    assert "13 题" in response.text
    # range syntax is documented on the page
    assert "1,3,8-12" in response.text


def test_configure_lists_completed_samples_with_review_links(client, completed_unit):
    response = client.get(f"/corpora/{completed_unit.corpus_id}/configure")
    assert response.status_code == 200
    assert completed_unit.package.passage.title in response.text
    assert f"/practice/{completed_unit.id}" in response.text
    assert f"/practice/{completed_unit.id}/analysis" in response.text
    assert "在线试做" in response.text
    assert "批准这一篇" in response.text


def test_sample_endpoint_reports_a_missing_api_key(client, web_service, sample_txt, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    manifest = web_service.import_source(sample_txt)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/sample", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("sample=no-key")
    page = client.get(response.headers["location"])
    assert "没有配置" in page.text and "DEEPSEEK_API_KEY" in page.text


def test_sample_endpoint_rejects_a_bad_type_combination(client, web_service, sample_txt):
    web_service.config.deepseek_api_key = SecretStr("sk-test-key")
    manifest = web_service.import_source(sample_txt)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/sample",
        data={
            "difficulty": "standard",
            "question_types": ["matching_headings", "matching_headings"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("sample=bad-types")
    assert "三种" in client.get(response.headers["location"]).text


def test_sample_endpoint_queues_work_and_records_the_outcome(
    client, web_service, sample_txt, monkeypatch
):
    """The web request only queues; a worker runs the sample and records the outcome."""

    from app.pipeline.tasks import sample_status_path
    from app.pipeline.worker import JobWorker

    web_service.config.deepseek_api_key = SecretStr("sk-test-key")
    manifest = web_service.import_source(sample_txt)
    calls: list[tuple] = []

    def fake_generate_sample(self, corpus_id, difficulty, question_types):
        calls.append((corpus_id, difficulty.value, [value.value for value in question_types]))

    monkeypatch.setattr(type(web_service), "generate_sample", fake_generate_sample)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/sample",
        data={"difficulty": "foundation"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("sample=started")

    # Nothing was generated inside the request: it is a queued job now.
    assert calls == []
    queued = web_service.queue.list_jobs()
    assert len(queued) == 1
    assert queued[0].kind == "reading_sample"
    assert queued[0].status == "queued"
    assert queued[0].payload["question_types"] == [
        "matching_headings",
        "true_false_not_given",
        "sentence_completion",
    ]

    finished = JobWorker(web_service).run_once()

    assert finished is not None and finished.status == "completed"
    assert calls == [
        (
            manifest.corpus.id,
            "foundation",
            ["matching_headings", "true_false_not_given", "sentence_completion"],
        )
    ]
    status_file = sample_status_path(web_service, manifest.corpus.id)
    assert status_file.is_file()
    assert "completed" in status_file.read_text(encoding="utf-8")
    page = client.get(f"/corpora/{manifest.corpus.id}/configure")
    assert "最近一次样篇：completed" in page.text


def test_sample_endpoint_is_idempotent_for_a_repeated_request(
    client, web_service, sample_txt
):
    """A double-clicked sample must not create a second paid job."""

    web_service.config.deepseek_api_key = SecretStr("sk-test-key")
    manifest = web_service.import_source(sample_txt)

    for _ in range(2):
        response = client.post(
            f"/corpora/{manifest.corpus.id}/sample",
            data={"difficulty": "standard"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    jobs = web_service.queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].kind == "reading_sample"


def test_configuration_page_shows_estimate_and_approval_gate(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    response = client.get(f"/corpora/{manifest.corpus.id}/configure")
    assert response.status_code == 200
    assert "Token" in response.text
    assert "批量生成已锁定" in response.text


def test_job_index_lists_tasks_with_readable_names_and_progress(
    client, web_service, completed_unit
) -> None:
    """任务页此前没有任何入链：离开跳转就找不回来，这里给出集中入口。"""

    web_service.repository.create_job(
        "job-1",
        completed_unit.corpus_id,
        "running",
        {"unit_ids": [completed_unit.id], "ordinals": [1, 2, 3], "difficulty": "standard"},
    )
    web_service.repository.assign_units_to_job([completed_unit.id], "job-1")

    page = client.get("/jobs")
    assert page.status_code == 200
    assert "第 1–3 章阅读生成任务" in page.text
    assert 'href="/jobs/job-1"' in page.text
    assert "1 / 1 单元完成" in page.text
    # 状态显示中文，而不是把 queued/running 这类内部值直接丢给用户
    assert "生成中" in page.text
    assert ">running<" not in page.text


def test_job_index_surfaces_failed_tasks_and_filters_them(
    client, web_service, sample_txt, completed_unit
) -> None:
    manifest = web_service.import_source(sample_txt)
    failed_unit = next(unit for unit in manifest.units if unit.id != completed_unit.id)
    web_service.repository.transition(failed_unit.id, UnitStatus.INDEXED, UnitStatus.FAILED)
    web_service.repository.create_job(
        "job-failed",
        manifest.corpus.id,
        "failed",
        {"unit_ids": [failed_unit.id], "ordinals": [failed_unit.ordinal]},
    )
    web_service.repository.assign_units_to_job([failed_unit.id], "job-failed")
    web_service.repository.create_job(
        "job-done",
        completed_unit.corpus_id,
        "completed",
        {"unit_ids": [completed_unit.id], "ordinals": [1]},
    )
    web_service.repository.assign_units_to_job([completed_unit.id], "job-done")

    all_jobs = client.get("/jobs")
    assert 'href="/jobs/job-failed"' in all_jobs.text
    assert "失败 1 个单元" in all_jobs.text
    assert "查看并重试" in all_jobs.text

    only_failed = client.get("/jobs?status=failed")
    assert 'href="/jobs/job-failed"' in only_failed.text
    assert 'href="/jobs/job-done"' not in only_failed.text


def test_job_index_is_reachable_from_the_navigation_and_the_corpus_card(
    client, web_service, completed_unit
) -> None:
    """导航里要有入口，材料卡上的"最近任务"也要能点进任务页。"""

    web_service.repository.create_job(
        "job-1", completed_unit.corpus_id, "completed", {"unit_ids": [completed_unit.id]}
    )

    home = client.get("/")
    assert 'href="/jobs"' in home.text

    corpora = client.get("/corpora")
    assert f'href="/jobs?corpus={completed_unit.corpus_id}"' in corpora.text
    assert 'href="/jobs/job-1"' in corpora.text
    assert "最近任务" in corpora.text
    assert "已完成" in corpora.text


def test_job_monitor_pause_and_unit_json(client, web_service, completed_unit):
    web_service.repository.create_job(
        "job-1",
        completed_unit.corpus_id,
        "running",
        {"unit_ids": [completed_unit.id]},
    )
    web_service.repository.assign_units_to_job([completed_unit.id], "job-1")
    detail = client.get("/jobs/job-1")
    assert detail.status_code == 200
    assert completed_unit.id in detail.text
    assert 'id="job-connection"' in detail.text
    assert 'src="/static/request.js?v=1"' in detail.text
    assert 'src="/static/jobs.js?v=2"' in detail.text

    paused = client.post("/jobs/job-1/pause", follow_redirects=False)
    assert paused.status_code == 303
    assert web_service.repository.get_job("job-1")["status"] == "paused"
    units = client.get("/jobs/job-1/units?after=0").json()
    assert units["job_status"] == "paused"
    assert units["units"][0]["id"] == completed_unit.id


def test_start_job_requires_sample_approval(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/jobs",
        data={"range_spec": "1-1"},
    )
    assert response.status_code == 409


def test_job_vocabulary_covers_every_real_pipeline_status() -> None:
    """库里存的是 completed / completed_with_errors：漏一个，界面上就露出英文枚举。"""

    from app.pipeline import queue
    from app.web.glossary import (
        ACTIVE_JOB_STATUSES,
        JOB_KIND_LABELS,
        JOB_STATUS_LABELS,
        job_kind_label,
        job_status_class,
        job_status_label,
    )

    statuses = {
        queue.QUEUED,
        queue.RUNNING,
        queue.PAUSED,
        queue.BLOCKED,
        queue.COMPLETED,
        queue.COMPLETED_WITH_ERRORS,
        queue.FAILED,
        queue.CANCELLED,
    }
    assert statuses == set(JOB_STATUS_LABELS)
    # "还有人打算做完它"的集合要和队列自己的定义一致，否则筛选会少显示任务
    assert set(ACTIVE_JOB_STATUSES) == set(queue.ACTIVE_STATUSES)
    for value in statuses:
        assert not job_status_label(value).isascii(), value
        assert job_status_class(value) == value.replace("_", "-")
    # 未知状态说人话，而不是把内部值漏到界面上
    assert job_status_label("brand_new_state") == "状态未知"
    assert job_status_class("brand_new_state") == "unknown"

    kinds = {queue.READING_KIND, queue.SAMPLE_KIND, queue.NOVEL_KIND}
    assert kinds == set(JOB_KIND_LABELS)
    for kind in kinds:
        assert not job_kind_label(kind).isascii(), kind


def test_units_waiting_for_a_human_are_reachable_under_the_failed_filter(
    client, web_service, sample_txt, completed_unit
) -> None:
    """批次跑完但有待确认单元时任务会变成 completed_with_errors，筛选不能漏掉它。"""

    manifest = web_service.import_source(sample_txt)
    review_unit = next(unit for unit in manifest.units if unit.id != completed_unit.id)
    web_service.repository.transition(review_unit.id, UnitStatus.INDEXED, UnitStatus.NEEDS_REVIEW)
    web_service.repository.create_job(
        "job-review",
        manifest.corpus.id,
        "completed_with_errors",
        {"unit_ids": [review_unit.id], "ordinals": [review_unit.ordinal]},
    )
    web_service.repository.assign_units_to_job([review_unit.id], "job-review")

    page = client.get("/jobs")
    assert "部分失败" in page.text
    assert "待人工确认 1 个单元" in page.text
    assert "查看并重试" in page.text
    # 内部枚举不出现在页面上
    assert "completed_with_errors" not in page.text

    only_failed = client.get("/jobs?status=failed")
    assert 'href="/jobs/job-review"' in only_failed.text

    finished = client.get("/jobs?status=finished")
    assert 'href="/jobs/job-review"' not in finished.text

