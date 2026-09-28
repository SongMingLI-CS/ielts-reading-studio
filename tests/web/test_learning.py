from types import SimpleNamespace

from app.agents.base import ModelResult
from app.learning.service import LearningService
from app.learning.sources import parse_markdown


def document():
    return parse_markdown(
        "# Arrays\n\nAn ndarray is a multidimensional array.\n\n```python\nx = 1\n```",
        title="Arrays",
        topic="numpy",
        version="2.5",
        source_url="https://numpy.org/doc/stable/",
    )


def guide_payload(quote="An ndarray is a multidimensional array."):
    question = {
        "kind": "concept",
        "prompt": "What is an ndarray?",
        "choices": ["Array", "String"],
        "correct_index": 0,
        "explanation": "多维数组。",
        "evidence_quote": quote,
    }
    return {
        "glossary": [
            {
                "term": "multidimensional",
                "chinese": "多维的",
                "english_explanation": "Having multiple dimensions.",
            }
        ],
        "concepts": [{"name": "ndarray", "explanation": "NumPy 的多维数组。"}],
        "questions": [
            question,
            {
                **question,
                "kind": "english",
                "prompt": "What does multidimensional mean?",
            },
        ],
    }


def save(web_service):
    service = LearningService(web_service)
    return service, service.save_document(document())


def test_library_reader_and_duplicate_import(client, web_service):
    service, doc = save(web_service)
    service.save_document(document())
    assert len(service.list_documents()) == 1
    assert client.get("/learn").status_code == 200
    page = client.get(f"/learn/{doc.id}").text
    assert "An ndarray is a multidimensional array." in page
    assert "x = 1" in page
    assert "2.5" in page
    assert client.get("/learn/not-found").status_code == 404


def test_guide_is_grounded_cached_and_quiz_scores_persist(client, web_service):
    service, doc = save(web_service)
    calls = []

    def complete(request):
        calls.append(request)
        return ModelResult(payload=guide_payload(), raw_text="{}")

    web_service._provider = SimpleNamespace(complete_json=complete)
    path = f"/api/learn/{doc.id}/{doc.sections[0].id}"
    assert client.post(path + "/guide").status_code == 200
    assert client.post(path + "/guide").status_code == 200
    assert len(calls) == 1
    assert "x = 1" in calls[0].user
    result = client.post(path + "/quiz", json={"answers": [0, 1]})
    assert result.status_code == 200
    assert result.json()["score"] == 1
    assert service.progress(doc.id)[doc.sections[0].id]["score"] == 1
    assert client.post(path + "/quiz", json={"answers": [99, 0]}).status_code == 422


def test_invented_evidence_is_not_saved(client, web_service):
    service, doc = save(web_service)
    web_service._provider = SimpleNamespace(
        complete_json=lambda _: ModelResult(
            payload=guide_payload("Invented source."), raw_text="{}"
        )
    )
    result = client.post(f"/api/learn/{doc.id}/{doc.sections[0].id}/guide")
    assert result.status_code == 502
    assert service.get_guide(doc.id, doc.sections[0].id) is None


def test_progress_notes_restore_and_csrf_is_required(client, raw_client, web_service):
    service, doc = save(web_service)
    path = f"/api/learn/{doc.id}/{doc.sections[0].id}/progress"
    assert raw_client.post(path, json={"completed": True}).status_code == 403
    assert (
        client.post(
            path, json={"completed": True, "notes": "Remember shape."}
        ).status_code
        == 200
    )
    assert "Remember shape." in client.get(f"/learn/{doc.id}").text
    assert client.post(path, json={"completed": False}).status_code == 200
    assert service.progress(doc.id)[doc.sections[0].id]["notes"] == "Remember shape."


def test_markdown_upload_and_version_validation(client):
    response = client.post(
        "/learn/upload",
        data={"title": "Python", "topic": "python", "version": "3.13"},
        files={"source": ("guide.md", b"# Python\n\nOriginal text.", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get(response.headers["location"]).status_code == 200
    assert (
        client.post(
            "/learn/upload",
            data={"title": "Python", "topic": "python", "version": "3.13"},
            files={"source": ("guide.exe", b"bad")},
        ).status_code
        == 415
    )


def test_remote_import_uses_document_fetcher(client, monkeypatch):
    monkeypatch.setattr(
        "app.web.routes_learning.fetch_official_document", lambda _: document()
    )
    response = client.post(
        "/learn/import",
        data={"url": "https://numpy.org/doc/stable/"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_markdown_formatting_does_not_render_untrusted_html(client):
    response = client.post(
        "/learn/upload",
        data={"title": "Safe document", "topic": "python", "version": "3.13"},
        files={
            "source": (
                "guide.md",
                b"# Test\n\n**Bold**\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))",
            )
        },
    )
    assert "<strong>Bold</strong>" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert 'href="javascript:' not in response.text


def test_guide_quotes_can_normalize_whitespace_but_save_the_original_quote(
    client, web_service
):
    service = LearningService(web_service)
    doc = service.save_document(
        parse_markdown(
            "# Arrays\n\nAn ndarray is a\nmultidimensional array.",
            title="Arrays",
            topic="numpy",
            version="2.5",
            source_url="",
        )
    )
    web_service._provider = SimpleNamespace(
        complete_json=lambda _: ModelResult(payload=guide_payload(), raw_text="{}")
    )
    result = client.post(f"/api/learn/{doc.id}/{doc.sections[0].id}/guide")
    assert result.status_code == 200
    assert (
        result.json()["questions"][0]["evidence_quote"]
        == "An ndarray is a\nmultidimensional array."
    )
