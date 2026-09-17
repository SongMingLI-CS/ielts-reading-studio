import json

from app.models import UnitStatus


def test_upload_imports_without_api_key(client, sample_txt, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with sample_txt.open("rb") as stream:
        response = client.post(
            "/corpora/import",
            files={"source": ("book.txt", stream, "text/plain")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert "章节解析" in preview.text
    assert "第一章" in preview.text
    corpus_id = response.headers["location"].split("/")[2]
    source_path = client.app.state.service.repository.get_corpus(corpus_id).source_path
    assert sample_txt.name not in source_path
    assert corpus_id in source_path


def test_upload_rejects_unsupported_extension(client):
    response = client.post(
        "/corpora/import",
        files={"source": ("payload.exe", b"not a book", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_corpus_index_and_paginated_preview(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    index = client.get("/corpora")
    assert manifest.corpus.name in index.text
    preview = client.get(f"/corpora/{manifest.corpus.id}/preview?page=1&page_size=1")
    assert preview.status_code == 200
    assert "第一章" in preview.text
    assert "第二章" not in preview.text


def test_corpus_index_shows_progress_and_next_step(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    page = client.get("/corpora")
    assert page.status_code == 200
    assert "材料库" in page.text
    assert "生成单元" in page.text
    assert "可练习" in page.text
    assert "待生成样篇" in page.text
    assert f"/corpora/{manifest.corpus.id}/configure" in page.text
    assert f"/corpora/{manifest.corpus.id}/preview" in page.text
    assert "导入 → 校准" not in page.text


def test_corpus_index_flags_a_missing_source(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    sample_txt.unlink()
    page = client.get("/corpora")
    assert "源文件已不在" in page.text
    assert str(manifest.corpus.id) in page.text


def test_preview_offers_pagination_and_structured_edits(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    page = client.get(f"/corpora/{corpus_id}/preview?page=1&page_size=1")
    assert "第 1 / 2 页" in page.text
    assert f"/corpora/{corpus_id}/preview?page=2&page_size=1" in page.text
    assert f'action="/corpora/{corpus_id}/boundaries/edit"' in page.text
    assert 'name="action" value="merge_next"' in page.text
    assert 'name="action" value="split_before_paragraph"' in page.text
    assert "章节 ID" in page.text
    assert manifest.chapters[0].id[:10] in page.text


def test_preview_freezes_boundary_edits_after_generation(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    unit = manifest.units[0]
    web_service.repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)
    page = client.get(f"/corpora/{manifest.corpus.id}/preview")
    assert "边界已冻结" in page.text
    assert 'name="action" value="merge_next"' not in page.text
    response = client.post(
        f"/corpora/{manifest.corpus.id}/boundaries/edit",
        data={"action": "merge_next", "chapter_id": manifest.chapters[0].id},
    )
    assert response.status_code == 409


def test_form_boundary_edit_merges_two_chapters(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    original = sample_txt.read_bytes()
    response = client.post(
        f"/corpora/{corpus_id}/boundaries/edit",
        data={
            "action": "merge_next",
            "chapter_id": manifest.chapters[0].id,
            "title": "合并后的第一章",
            "page": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/corpora/{corpus_id}/preview?page=1"
    assert web_service.repository.get_corpus(corpus_id).chapter_count == 1
    chapters = web_service.repository.list_source_chapters(corpus_id)
    assert chapters[0].chapter_title == "合并后的第一章"
    assert sample_txt.read_bytes() == original


def test_form_boundary_edit_rejects_bad_requests(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    unknown = client.post(
        f"/corpora/{corpus_id}/boundaries/edit",
        data={"action": "merge_next", "chapter_id": "does-not-exist"},
    )
    assert unknown.status_code == 422
    bad_action = client.post(
        f"/corpora/{corpus_id}/boundaries/edit",
        data={"action": "delete_everything", "chapter_id": manifest.chapters[0].id},
    )
    assert bad_action.status_code == 422
    missing_title = client.post(
        f"/corpora/{corpus_id}/boundaries/edit",
        data={"action": "rename", "chapter_id": manifest.chapters[0].id},
    )
    assert missing_title.status_code == 422


def test_import_page_explains_the_formats_and_limits(client):
    page = client.get("/corpora/import")
    assert page.status_code == 200
    assert "250 MB" in page.text
    assert "EPUB" in page.text
    assert "drop-zone" in page.text
    assert "导入后会发生什么" in page.text


def test_boundary_override_is_saved_separately(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    original = sample_txt.read_bytes()
    response = client.post(
        f"/corpora/{manifest.corpus.id}/boundaries",
        data={
            "overrides": json.dumps(
                [{"action": "merge_next", "chapter_id": manifest.chapters[0].id}],
                ensure_ascii=False,
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    paths = list(web_service.config.output_dir.rglob("boundary-overrides.json"))
    assert len(paths) == 1
    assert "merge_next" in paths[0].read_text(encoding="utf-8")
    assert web_service.repository.get_corpus(manifest.corpus.id).chapter_count == 1
    assert len(web_service.repository.list_source_chapters(manifest.corpus.id)) == 1
    assert sample_txt.read_bytes() == original
