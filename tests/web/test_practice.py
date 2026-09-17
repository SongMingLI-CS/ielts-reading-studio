from app.models import QuestionType
from app.web.routes_practice import _matches, _option_variants


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


def test_session_form_can_submit_without_javascript(client, completed_unit):
    response = client.get(f"/practice/{completed_unit.id}")
    assert f'action="/practice/{completed_unit.id}/submit"' in response.text
    assert 'method="post"' in response.text
    assert 'name="attempt_id"' in response.text
    assert 'name="elapsed_seconds"' in response.text


def test_session_resumes_the_saved_server_draft(client, completed_unit):
    client.post(
        f"/practice/{completed_unit.id}/save",
        json={
            "attempt_id": "draft-resume",
            "answers": {"1": "water"},
            "elapsed_seconds": 42,
        },
    )
    response = client.get(f"/practice/{completed_unit.id}")
    assert response.status_code == 200
    assert 'data-attempt-id="draft-resume"' in response.text
    assert "data-saved-answers='{\"1\": \"water\"}'" in response.text
    assert 'data-resume-seconds="42"' in response.text


def test_fresh_session_ignores_the_saved_draft(client, completed_unit):
    client.post(
        f"/practice/{completed_unit.id}/save",
        json={"attempt_id": "draft-old", "answers": {"1": "water"}, "elapsed_seconds": 42},
    )
    response = client.get(f"/practice/{completed_unit.id}?fresh=1")
    assert response.status_code == 200
    assert 'data-attempt-id=""' in response.text
    assert "data-saved-answers='{}'" in response.text
    assert 'data-resume-seconds="0"' in response.text


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
    assert response.json()["redirect_url"] == (
        f"/practice/{completed_unit.id}/result/attempt-submit"
    )

    saved = client.app.state.service.repository.get_practice_attempt("attempt-submit")
    assert saved is not None
    assert saved["status"] == "submitted"
    assert saved["score"] == 1


def test_native_form_post_scores_redirects_and_persists(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        data={"q1": "water supply", "q2": "wrong", "elapsed_seconds": "65"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"/practice/{completed_unit.id}/result/")
    attempt_id = location.rsplit("/", 1)[-1]

    saved = client.app.state.service.repository.get_practice_attempt(attempt_id)
    assert saved is not None
    assert saved["unit_id"] == completed_unit.id
    assert saved["status"] == "submitted"
    assert saved["total"] == 12
    assert saved["score"] == 1
    assert saved["payload"]["answers"] == {"1": "water supply", "2": "wrong"}
    assert saved["payload"]["elapsed_seconds"] == 65


def test_native_form_post_keeps_a_supplied_attempt_id(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        data={"attempt_id": "form-attempt", "q1": "water supply"},
        follow_redirects=False,
    )
    assert response.headers["location"] == (
        f"/practice/{completed_unit.id}/result/form-attempt"
    )
    saved = client.app.state.service.repository.get_practice_attempt("form-attempt")
    assert saved["status"] == "submitted"


def test_native_form_post_result_page_renders_answers(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        data={"attempt_id": "form-navigated", "q1": "water supply"},
    )
    assert response.status_code == 200
    assert "你的答案" in response.text
    assert "原文给出了答案。" in response.text


def test_unanswered_questions_are_scored_wrong(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-blank", "answers": {}},
    )
    assert response.status_code == 200
    assert response.json()["correct"] == 0
    assert response.json()["total"] == 12
    assert all(not result["correct"] for result in response.json()["results"])


def test_result_page_shows_answers_evidence_and_explanations(client, completed_unit):
    submitted = client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-result", "answers": {"1": "water supply"}, "elapsed_seconds": 90},
    )
    assert submitted.status_code == 200

    page = client.get(f"/practice/{completed_unit.id}/result/attempt-result")
    assert page.status_code == 200
    assert "你的答案" in page.text
    assert "正确答案" in page.text
    assert "原文给出了答案。" in page.text
    assert "<strong>1</strong>" in page.text
    assert "/ 12" in page.text
    assert "8%" in page.text
    assert f"/practice/{completed_unit.id}?fresh=1" in page.text
    assert (
        f"/practice/{completed_unit.id}/analysis?attempt=attempt-result" in page.text
    )


def test_result_page_rejects_unknown_or_foreign_attempts(client, completed_unit, needs_review_unit):
    assert (
        client.get(f"/practice/{completed_unit.id}/result/missing-attempt").status_code == 404
    )
    assert (
        client.get(f"/practice/{needs_review_unit.id}/result/attempt-result").status_code == 409
    )


def test_analysis_can_show_the_submitted_answers(client, completed_unit):
    client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-analysis", "answers": {"1": "water supply"}},
    )
    page = client.get(
        f"/practice/{completed_unit.id}/analysis?attempt=attempt-analysis"
    )
    assert page.status_code == 200
    assert "你的答案" in page.text
    assert "water supply" in page.text


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


def test_practice_center_reports_attempt_history(client, completed_unit):
    client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-history", "answers": {"1": "water supply"}},
    )
    response = client.get("/practice")
    assert response.status_code == 200
    assert "1 篇可练习" in response.text
    assert "学习概览" in response.text
    assert "私有存储" in response.text
    assert "1 / 12" in response.text
    assert f"/practice/{completed_unit.id}/result/attempt-history" in response.text


def test_heading_answers_accept_the_option_letter_or_full_heading():
    heading = "viii. The central protagonist's background and role"
    assert _matches("viii", [heading, "viii"], QuestionType.MATCHING_HEADINGS)
    assert _matches(heading, [heading, "viii"], QuestionType.MATCHING_HEADINGS)
    assert not _matches("ix", [heading, "viii"], QuestionType.MATCHING_HEADINGS)


def test_multiple_choice_accepts_single_letter_and_multi_select():
    options = ["A. First reason", "B. Second reason", "C. Third reason"]
    accepted = _option_variants(options, "B. Second reason")
    assert _matches("b. second reason", accepted, QuestionType.MULTIPLE_CHOICE)
    assert _matches("B", accepted, QuestionType.MULTIPLE_CHOICE)
    assert not _matches("C", accepted, QuestionType.MULTIPLE_CHOICE)
    assert _matches(["A", "B"], ["A, B"], QuestionType.MULTIPLE_CHOICE)
    assert not _matches(["A", "C"], ["A, B"], QuestionType.MULTIPLE_CHOICE)


def test_completion_answers_ignore_case_and_spacing():
    assert _matches("  Flexible   Mind ", ["flexible mind"], QuestionType.SUMMARY_COMPLETION)
    assert not _matches("", ["flexible mind"], QuestionType.SUMMARY_COMPLETION)
