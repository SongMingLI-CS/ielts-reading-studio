from __future__ import annotations

from app.agents.base import ModelResult, ProviderRateLimitError


def payload():
    return {
        "task_fulfilment": {"band": 7.0, "feedback": "A clear response."},
        "coherence_and_cohesion": {"band": 7.0, "feedback": "Ideas progress logically."},
        "lexical_resource": {"band": 6.5, "feedback": "Vocabulary is sufficient."},
        "grammatical_range_and_accuracy": {"band": 6.5, "feedback": "Minor errors occur."},
        "general_feedback": "The essay addresses the prompt.",
        "strengths": ["Relevant ideas"],
        "improvements": ["Develop examples further"],
        "sample_answer": None,
    }


def request_body():
    return {
        "task_type": "task_2",
        "question": "Some people prefer cities. Discuss both views and give your opinion.",
        "essay": "Cities provide opportunities, while rural areas offer calm. " * 8,
    }


def test_writing_page_is_available_from_site_navigation(client):
    response = client.get("/writing")

    assert response.status_code == 200
    assert "IELTS 写作评估" in response.text
    assert 'id="writing-form"' in response.text
    assert 'id="writing-loading"' in response.text
    assert 'id="writing-stage"' in response.text
    assert 'src="/static/request.js?v=1"' in response.text
    assert 'src="/static/writing.js?v=2"' in response.text
    assert 'href="/writing" aria-current="page"' in response.text


def test_home_page_links_to_writing_evaluation(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/writing"' in response.text
    assert "开始评估作文" in response.text


def test_writing_evaluation_api_returns_validated_result_and_persists(client, web_service):
    class Provider:
        def complete_json(self, request):
            return ModelResult(payload=payload(), raw_text="{}", model=request.model)

    web_service._provider = Provider()

    response = client.post("/api/writing/evaluate", json=request_body())

    assert response.status_code == 200
    result = response.json()
    assert result["overall_band"] == 7.0
    assert result["task_response"]["band"] == 7.0
    assert result["task_achievement"] is None
    assert web_service.repository.get_writing_evaluation(result["id"]) is not None


def test_writing_evaluation_api_validates_input_before_provider(client):
    response = client.post(
        "/api/writing/evaluate",
        json={"task_type": "task_3", "question": "short", "essay": "short"},
    )

    assert response.status_code == 422


def test_writing_evaluation_api_maps_provider_failure(client, web_service):
    class Provider:
        def complete_json(self, request):
            raise ProviderRateLimitError("Provider rate limit")

    web_service._provider = Provider()

    response = client.post("/api/writing/evaluate", json=request_body())

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "rate_limited"


def test_writing_evaluation_api_maps_invalid_model_payload(client, web_service):
    class Provider:
        def complete_json(self, request):
            return ModelResult(payload={"overall_band": 9}, raw_text="{}")

    web_service._provider = Provider()

    response = client.post("/api/writing/evaluate", json=request_body())

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_model_response"