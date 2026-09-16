def test_practice_page_does_not_render_answer_key(client, completed_unit):
    answer = completed_unit.package.question_groups[0].questions[0].answer
    response = client.get(f"/practice/{completed_unit.id}")
    assert response.status_code == 200
    assert answer not in response.text


def test_submission_scores_normalized_exact_answers(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"answers": {"1": "  WATER   SUPPLY "}},
    )
    assert response.status_code == 200
    assert response.json()["correct"] == 1
    assert response.json()["results"][0]["answer"] == "water supply"


def test_analysis_is_available_only_for_completed_package(client, completed_unit, needs_review_unit):
    assert client.get(f"/practice/{completed_unit.id}/analysis").status_code == 200
    assert client.get(f"/practice/{needs_review_unit.id}/analysis").status_code == 409
