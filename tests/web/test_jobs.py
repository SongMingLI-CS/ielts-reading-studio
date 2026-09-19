from pydantic import SecretStr


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
