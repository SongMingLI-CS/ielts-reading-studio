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
    assert "章节解析预览" in preview.text
    assert "第一章" in preview.text


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


def test_boundary_override_is_saved_separately(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/boundaries",
        data={"overrides": "在第二段前切分"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    paths = list(web_service.config.output_dir.rglob("boundary-overrides.json"))
    assert len(paths) == 1
    assert "在第二段前切分" in paths[0].read_text(encoding="utf-8")
