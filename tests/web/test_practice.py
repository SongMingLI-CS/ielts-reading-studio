def test_practice_page_does_not_render_answer_key(client, completed_unit):
    answer = completed_unit.package.question_groups[0].questions[0].answer
    response = client.get(f"/practice/{completed_unit.id}")
    assert response.status_code == 200
    assert answer not in response.text
    assert 'class="practice-shell"' in response.text
    assert 'id="autosave-status"' in response.text
    assert 'class="mobile-pane-tabs"' in response.text
    assert 'data-show-pane="reading"' in response.text
    assert 'data-show-pane="questions"' in response.text


def test_practice_center_lists_completed_packages(client, completed_unit):
    response = client.get("/practice")
    assert response.status_code == 200
    assert completed_unit.package.passage.title in response.text
    assert f'/practice/{completed_unit.id}' in response.text
    assert "1 篇可练习" in response.text
    assert "学习概览" in response.text
    assert "私有存储" in response.text


def test_submission_scores_normalized_exact_answers(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-submit", "answers": {"1": "  WATER   SUPPLY "}},
    )
    assert response.status_code == 200
    assert response.json()["correct"] == 1
    assert response.json()["attempt_id"] == "attempt-submit"
    assert response.json()["results"][0]["answer"] == "water supply"

    saved = client.app.state.service.repository.get_practice_attempt("attempt-submit")
    assert saved is not None
    assert saved["status"] == "submitted"
    assert saved["score"] == 1


def test_practice_draft_is_saved_in_local_sqlite(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/save",
        json={"attempt_id": "attempt-draft", "answers": {"1": "water"}, "elapsed_seconds": 42},
    )
    assert response.status_code == 200
    assert response.json() == {"attempt_id": "attempt-draft", "saved": True}

    saved = client.app.state.service.repository.get_practice_attempt("attempt-draft")
    assert saved is not None
    assert saved["unit_id"] == completed_unit.id
    assert saved["status"] == "in_progress"
    assert saved["payload"]["answers"] == {"1": "water"}
    assert saved["payload"]["elapsed_seconds"] == 42


def test_analysis_is_available_only_for_completed_package(client, completed_unit, needs_review_unit):
    assert client.get(f"/practice/{completed_unit.id}/analysis").status_code == 200
    assert client.get(f"/practice/{needs_review_unit.id}/analysis").status_code == 409
