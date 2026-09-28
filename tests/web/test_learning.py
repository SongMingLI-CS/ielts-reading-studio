from types import SimpleNamespace

import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import update

from app.agents.base import ModelResult
from app.learning.service import LearningService
from app.learning.sources import parse_markdown
from app.storage.database import study_documents


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
    source = BeautifulSoup(page, "html.parser").select_one(".learn-source-text")
    assert "An ndarray is a multidimensional array." in source.get_text()
    assert "x = 1" in page
    assert "2.5" in page
    assert client.get("/learn/not-found").status_code == 404


def vocabulary_payload(
    term="multidimensional", quote="An ndarray is a multidimensional array."
):
    return {
        "words": [
            {
                "term": term,
                "chinese": "多维的",
                "usage_note": "multi- 表示多个，dimensional 表示维度。",
                "source_quote": quote,
            }
        ]
    }


def test_import_immediately_marks_difficult_words_without_model_calls(
    client, web_service
):
    def unexpected_call(_):
        raise AssertionError("Import and common word lookup must not call the model")

    web_service._provider = SimpleNamespace(complete_json=unexpected_call)
    response = client.post(
        "/learn/upload",
        data={"title": "English reading", "topic": "numpy", "version": "2.5"},
        files={
            "source": (
                "guide.md",
                b"# Arrays\n\nAn arbitrary array.\n\n```python\narray = 'arbitrary'\n```",
            )
        },
    )
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert (
        soup.select_one('[data-reading-word="arbitrary"]')["data-chinese"]
        == "任意的；未作特定限制的"
    )
    assert not soup.select("code [data-reading-word]")
    assert soup.select_one("pre code").text == "array = 'arbitrary'"


def test_contextual_words_are_grounded_cached_and_preserve_existing_progress(
    client, web_service
):
    service = LearningService(web_service)
    doc = service.save_document(
        parse_markdown(
            "# Arrays\n\nAn ndarray is a\nmultidimensional array.\n\n```python\nx = 1\n```",
            title="Arrays",
            topic="numpy",
            version="2.5",
            source_url="",
        )
    )
    section = doc.sections[0]
    service.update_progress(
        doc.id,
        section.id,
        {"completed": True, "notes": "Keep this note", "score": 2, "answers": [0, 1]},
    )
    calls = []

    def complete(request):
        calls.append(request)
        return ModelResult(payload=vocabulary_payload(), raw_text="{}")

    web_service._provider = SimpleNamespace(complete_json=complete)
    path = f"/api/learn/{doc.id}/{section.id}/vocabulary"
    response = client.post(path)
    assert response.status_code == 200
    assert (
        response.json()["words"][0]["source_quote"]
        == "An ndarray is a\nmultidimensional array."
    )
    assert client.post(path).json() == response.json()
    assert len(calls) == 1 and calls[0].stage == "reading_vocabulary"
    assert "x = 1" not in calls[0].user
    progress = service.progress(doc.id)[section.id]
    assert progress["completed"] and progress["notes"] == "Keep this note"
    assert progress["score"] == 2 and progress["answers"] == [0, 1]
    assert service.get_document(doc.id).sections[0].text == section.text
    for query in ("", f"?section={section.id}"):
        page = BeautifulSoup(client.get(f"/learn/{doc.id}{query}").text, "html.parser")
        word = page.select_one('[data-reading-word="multidimensional"]')
        assert word["data-meaning-source"] == "本节语境释义"
        assert word["data-source-quote"] == "An ndarray is a\nmultidimensional array."
        assert "x = 1" in page.select_one(".learn-source-text").get_text()


@pytest.mark.parametrize(
    "term,quote",
    [
        ("multidimensional", "Invented multidimensional source."),
        ("view", "The review is conventional."),
        ("array", "The review is conventional."),
        ("invented", "An ndarray is a multidimensional array."),
    ],
)
def test_invalid_vocabulary_evidence_is_not_saved(client, web_service, term, quote):
    service = LearningService(web_service)
    doc = service.save_document(
        parse_markdown(
            "# Arrays\n\nAn ndarray is a multidimensional array.\n\nThe review is conventional.",
            title="Arrays",
            topic="numpy",
            version="2.5",
            source_url="",
        )
    )
    web_service._provider = SimpleNamespace(
        complete_json=lambda _: ModelResult(
            payload=vocabulary_payload(term, quote), raw_text="{}"
        )
    )
    assert (
        client.post(f"/api/learn/{doc.id}/{doc.sections[0].id}/vocabulary").status_code
        == 502
    )
    assert not service.progress(doc.id)


def test_vocabulary_requires_csrf_and_prose_and_known_section(
    client, raw_client, web_service
):
    service, doc = save(web_service)
    assert (
        raw_client.post(
            f"/api/learn/{doc.id}/{doc.sections[0].id}/vocabulary"
        ).status_code
        == 403
    )
    assert client.post(f"/api/learn/{doc.id}/not-found/vocabulary").status_code == 404
    code = service.save_document(
        parse_markdown(
            "# Code\n\n```python\narray = 'mutable'\n```",
            title="Code",
            topic="numpy",
            version="2.5",
            source_url="",
        )
    )
    assert (
        client.post(
            f"/api/learn/{code.id}/{code.sections[0].id}/vocabulary"
        ).status_code
        == 422
    )
    assert not service.progress(code.id)


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


def test_upload_above_old_limit_keeps_final_content_and_uses_bounded_sections(
    client, web_service
):
    source = b"# Manual\n\n" + b"Original content. " * 200000 + b"THE VERY LAST WORDS"
    response = client.post(
        "/learn/upload",
        data={"title": "Full manual", "topic": "python", "version": "3.14"},
        files={"source": ("manual.md", source)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    service = LearningService(web_service)
    doc = service.get_document(response.headers["location"].rsplit("/", 1)[-1])
    assert doc.sections[-1].text.endswith("THE VERY LAST WORDS")
    assert doc.content_characters >= len(source) - 20
    assert all(len(s.text) <= 12000 for s in doc.sections)
    assert "100 MB" in client.get("/learn").text


def test_upload_limit_respects_configuration_and_leaves_no_partial_document(
    client, web_service
):
    web_service.config.web_learning_max_upload_bytes = 1024 * 1024
    response = client.post(
        "/learn/upload",
        data={"title": "Manual", "topic": "python", "version": "3.14"},
        files={"source": ("manual.md", b"x" * (1024 * 1024 + 1))},
    )
    assert response.status_code == 413
    assert "1 MB" in response.text
    assert LearningService(web_service).list_documents() == []
    web_service.config.web_learning_max_upload_bytes = 100 * 1024 * 1024
    web_service.config.web_max_upload_bytes = 1024 * 1024
    assert "最大 1 MB" in client.get("/learn").text


def test_continuous_reader_includes_all_small_sections_and_study_mode_restores_notes(
    client, web_service
):
    service = LearningService(web_service)
    doc = service.save_document(
        parse_markdown(
            "# Intro\n\nFirst original paragraph.\n\n## Middle\n\nMiddle original paragraph.\n\n## End\n\nFinal original paragraph.",
            title="Three sections",
            topic="python",
            version="3.14",
            source_url="",
        )
    )
    service.update_progress(
        doc.id, "s2", {"notes": "My existing notes", "completed": True}
    )
    page = BeautifulSoup(client.get(f"/learn/{doc.id}").text, "html.parser")
    assert [
        s["data-learning-section"] for s in page.select("[data-learning-section]")
    ] == ["s1", "s2", "s3"]
    assert (
        page.select_one('[data-mark-read][data-section="s2"]')["aria-pressed"] == "true"
    )
    study = BeautifulSoup(client.get(f"/learn/{doc.id}?section=s2").text, "html.parser")
    assert [
        s["data-learning-section"] for s in study.select("[data-learning-section]")
    ] == ["s2"]
    assert study.select_one("#learning-notes").text == "My existing notes"
    assert client.get(f"/learn/{doc.id}?start=unknown").status_code == 404


def test_reading_pagination_covers_the_whole_manual_once_in_order(
    client, web_service, monkeypatch
):
    monkeypatch.setattr("app.web.routes_learning.READING_PAGE_CHARACTERS", 45)
    service = LearningService(web_service)
    doc = service.save_document(
        parse_markdown(
            "\n\n".join(
                f"## Section {i}\n\nOriginal content number {i}." for i in range(9)
            ),
            title="Nine sections",
            topic="python",
            version="3.14",
            source_url="",
        )
    )
    seen = []
    url = f"/learn/{doc.id}"
    previous_url = None
    previous_ids = []
    while url:
        soup = BeautifulSoup(client.get(url).text, "html.parser")
        seen.extend(
            s["data-learning-section"] for s in soup.select("[data-learning-section]")
        )
        links = soup.select(".learn-next a")
        previous = next((a["href"] for a in links if "上一页" in a.text), None)
        if previous_url:
            earlier = BeautifulSoup(client.get(previous).text, "html.parser")
            assert [
                s["data-learning-section"]
                for s in earlier.select("[data-learning-section]")
            ] == previous_ids
        previous_url = url
        previous_ids = [
            s["data-learning-section"] for s in soup.select("[data-learning-section]")
        ]
        url = next((a["href"] for a in links if "下一页" in a.text), None)
    assert seen == [s.id for s in doc.sections]


def test_library_reads_only_summary_and_supports_legacy_payloads(client, web_service):
    service, doc = save(web_service)
    summary = service.list_documents()[0]
    assert summary.content_characters == doc.content_characters
    assert not hasattr(summary, "sections")
    payload = doc.model_dump(
        exclude={"content_characters", "page_count", "section_count", "source_pages"}
    )
    import json

    with service.engine.begin() as connection:
        connection.execute(
            update(study_documents)
            .where(study_documents.c.id == doc.id)
            .values(payload=json.dumps(payload, default=str))
        )
    assert service.list_documents()[0].page_count == 1
    assert client.get("/learn").status_code == 200
    assert "1 篇原文" in client.get("/learn").text


def test_collection_import_failure_never_saves_partial_docs(
    client, web_service, monkeypatch
):
    def fail(_):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("app.web.routes_learning.fetch_collection", fail)
    assert client.post("/learn/collection", data={"topic": "python"}).status_code == 502
    assert LearningService(web_service).list_documents() == []
    assert (
        client.post("/learn/collection", data={"topic": "unknown"}).status_code == 422
    )
