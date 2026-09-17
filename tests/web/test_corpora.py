import json
from pathlib import Path

from app.models import UnitStatus
from app.planning.importer import MANIFEST_REBUILT_DIAGNOSTIC


def test_import_page_explains_the_formats_and_limits(client):
    page = client.get("/corpora/import")
    assert page.status_code == 200
    assert "250 MB" in page.text
    assert "EPUB" in page.text
    assert "drop-zone" in page.text
    assert "导入后会发生什么" in page.text


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


def test_reimporting_the_same_file_rebinds_a_stale_source_path(
    client, web_service, sample_txt, tmp_path
):
    manifest = web_service.import_source(sample_txt)
    units_before = web_service.repository.list_units(manifest.corpus.id)
    moved = tmp_path / "elsewhere" / sample_txt.name
    moved.parent.mkdir()
    moved.write_bytes(sample_txt.read_bytes())
    sample_txt.unlink()
    assert "源文件已不在" in client.get("/corpora").text

    with moved.open("rb") as stream:
        response = client.post(
            "/corpora/import",
            files={"source": ("book.txt", stream, "text/plain")},
            follow_redirects=False,
        )
    assert response.status_code == 303

    corpus = web_service.repository.get_corpus(manifest.corpus.id)
    assert Path(corpus.source_path).exists()
    assert corpus.source_path != manifest.corpus.source_path
    assert "源文件已不在" not in client.get("/corpora").text
    # the index, the units and the readable manifest must survive a rebind
    assert web_service.repository.get_corpus(manifest.corpus.id).chapter_count == 2
    assert len(web_service.repository.list_units(manifest.corpus.id)) == len(units_before)
    assert web_service.importer.load_manifest(corpus) is not None
    assert web_service.importer.load_manifest(corpus).corpus.source_path == corpus.source_path


def test_delete_page_lists_the_impact_and_needs_a_confirmation(
    client, web_service, sample_txt
):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    unit = manifest.units[0]
    web_service.repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)
    web_service.repository.save_practice_attempt(
        "attempt-delete", unit.id, status="submitted", score=1, total=2, payload={}
    )
    page = client.get(f"/corpora/{corpus_id}/delete")
    assert page.status_code == 200
    assert "章节索引" in page.text and "生成单元" in page.text
    assert "已成篇（可练习）" in page.text
    assert "练习记录" in page.text
    assert "源文件本身不会被删除" in page.text

    wrong = client.post(f"/corpora/{corpus_id}/delete", data={"confirm": "no"})
    assert wrong.status_code == 400
    assert "确认文字不匹配" in wrong.text
    assert web_service.repository.get_corpus(corpus_id) is not None

    cancel = client.post(f"/corpora/{corpus_id}/delete", data={"confirm": "取消吧"})
    assert cancel.status_code == 400
    assert web_service.repository.get_corpus(corpus_id) is not None


def test_delete_removes_rows_and_artifacts_but_keeps_the_source_file(
    client, web_service, sample_txt
):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    unit = manifest.units[0]
    web_service.repository.transition(unit.id, UnitStatus.INDEXED, UnitStatus.COMPLETED)
    web_service.repository.save_practice_attempt(
        "attempt-delete", unit.id, status="submitted", score=1, total=2, payload={}
    )
    manifest_dir = web_service.importer.manifest_path(manifest.corpus).parent
    package_file = web_service.store.root / "packages" / f"{unit.id}.json"
    package_file.parent.mkdir(parents=True, exist_ok=True)
    package_file.write_text("{}", encoding="utf-8")
    assert manifest_dir.exists()

    response = client.post(
        f"/corpora/{corpus_id}/delete",
        data={"confirm": corpus_id[:8]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/corpora?deleted=")

    assert web_service.repository.get_corpus(corpus_id) is None
    assert web_service.repository.list_units(corpus_id) == []
    assert web_service.repository.list_source_chapters(corpus_id) == []
    assert web_service.repository.list_practice_attempts(corpus_id) == []
    assert web_service.repository.get_practice_attempt("attempt-delete") is None
    assert not manifest_dir.exists()
    assert not package_file.exists()
    assert sample_txt.exists()

    index = client.get(response.headers["location"])
    assert "已删除" in index.text
    assert corpus_id[:8] not in index.text


def test_delete_leaves_other_corpora_untouched(client, web_service, sample_txt, tmp_path):
    first = web_service.import_source(sample_txt)
    other_txt = tmp_path / "second.txt"
    other_txt.write_text(
        "第三章 转折\n" + "转折。" * 300 + "\n第四章 收束\n" + "收束。" * 300,
        encoding="utf-8",
    )
    second = web_service.import_source(other_txt)

    response = client.post(
        f"/corpora/{first.corpus.id}/delete",
        data={"confirm": "删除"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert web_service.repository.get_corpus(first.corpus.id) is None
    survivor = web_service.repository.get_corpus(second.corpus.id)
    assert survivor is not None
    assert survivor.chapter_count == 2
    assert web_service.repository.list_units(second.corpus.id)
    assert web_service.importer.load_manifest(survivor) is not None


def test_delete_is_refused_while_units_are_running(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    unit = manifest.units[0]
    web_service.repository.transition(
        unit.id, UnitStatus.INDEXED, UnitStatus.AUTHOR_GENERATING
    )
    page = client.get(f"/corpora/{manifest.corpus.id}/delete")
    assert "当前无法删除" in page.text
    response = client.post(
        f"/corpora/{manifest.corpus.id}/delete", data={"confirm": "删除"}
    )
    assert response.status_code == 409
    assert web_service.repository.get_corpus(manifest.corpus.id) is not None


def test_rebind_endpoint_repairs_a_missing_source(client, web_service, sample_txt, tmp_path):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    units_before = len(web_service.repository.list_units(corpus_id))
    copy = tmp_path / "again" / sample_txt.name
    copy.parent.mkdir()
    copy.write_bytes(sample_txt.read_bytes())
    sample_txt.unlink()

    with copy.open("rb") as stream:
        response = client.post(
            f"/corpora/{corpus_id}/rebind",
            files={"source": ("book.txt", stream, "text/plain")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == f"/corpora/{corpus_id}/preview?rebound=1"
    corpus = web_service.repository.get_corpus(corpus_id)
    assert Path(corpus.source_path).exists()
    assert web_service.repository.get_corpus(corpus_id).chapter_count == 2
    assert len(web_service.repository.list_units(corpus_id)) == units_before
    page = client.get(f"/corpora/{corpus_id}/preview?rebound=1")
    assert "已重新绑定" in page.text


def test_rebind_endpoint_rejects_a_different_file(client, web_service, sample_txt, tmp_path):
    manifest = web_service.import_source(sample_txt)
    other = tmp_path / "other.txt"
    other.write_text("第一章 别的\n" + "别的内容。" * 400 + "\n第二章 也不一样\n" + "另一段。" * 400, encoding="utf-8")
    sample_txt.unlink()
    with other.open("rb") as stream:
        response = client.post(
            f"/corpora/{manifest.corpus.id}/rebind",
            files={"source": ("other.txt", stream, "text/plain")},
        )
    assert response.status_code == 409
    assert "哈希" in response.text
    assert not Path(web_service.repository.get_corpus(manifest.corpus.id).source_path).exists()


def test_rebind_endpoint_checks_the_extension(client, web_service, sample_txt):
    manifest = web_service.import_source(sample_txt)
    response = client.post(
        f"/corpora/{manifest.corpus.id}/rebind",
        files={"source": ("payload.exe", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_preview_offers_a_rebind_form_only_when_the_source_is_gone(
    client, web_service, sample_txt, tmp_path
):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    present = client.get(f"/corpora/{corpus_id}/preview")
    assert f'action="/corpora/{corpus_id}/rebind"' not in present.text
    sample_txt.unlink()
    missing = client.get(f"/corpora/{corpus_id}/preview")
    assert f'action="/corpora/{corpus_id}/rebind"' in missing.text
    assert "重新绑定源文件" in missing.text


def test_reimport_after_a_lost_manifest_rebuilds_it_from_the_index(
    client, web_service, sample_txt
):
    manifest = web_service.import_source(sample_txt)
    corpus_id = manifest.corpus.id
    units_before = len(web_service.repository.list_units(corpus_id))
    # a copied deployment can carry the SQLite index without the manifest file
    web_service.importer.manifest_path(manifest.corpus).unlink()
    assert web_service.importer.load_manifest(manifest.corpus) is None

    again = web_service.import_source(sample_txt)
    assert again.chapter_count == 2
    assert len(web_service.repository.list_units(corpus_id)) == units_before
    rebuilt = web_service.importer.load_manifest(
        web_service.repository.get_corpus(corpus_id)
    )
    assert rebuilt is not None
    assert MANIFEST_REBUILT_DIAGNOSTIC in rebuilt.diagnostics

    page = client.get("/corpora")
    assert "章节清单已按数据库索引重建" in page.text
    assert "<dt>解析置信度</dt>" not in page.text


def test_corpus_index_explains_how_to_fix_a_missing_source(client, web_service, sample_txt):
    web_service.import_source(sample_txt)
    sample_txt.unlink()
    page = client.get("/corpora")
    assert "重新绑定" in page.text
    assert "索引仍然可用" in page.text


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
