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
