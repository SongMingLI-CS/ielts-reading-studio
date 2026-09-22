"""Upload validation and filesystem path containment."""

from __future__ import annotations

import os

from app.security.uploads import check_magic, normalize_extension, safe_display_name


def test_extension_normalisation_ignores_paths_and_case():
    assert normalize_extension("novel.TXT") == ".txt"
    assert normalize_extension("../../etc/passwd.docx") == ".docx"
    assert normalize_extension("C:\\temp\\book.EPUB") == ".epub"
    assert normalize_extension(None) == ""


def test_display_names_lose_directories_and_control_characters():
    assert safe_display_name("../../etc/passwd") == "passwd"
    assert safe_display_name("a\x00b.txt") == "a_b.txt"
    assert safe_display_name(".") == "upload"
    assert safe_display_name(None) == "upload"


def test_magic_check_only_allows_real_container_files():
    assert check_magic(".docx", b"PK\x03\x04rest") is True
    assert check_magic(".docx", b"plain text") is False
    assert check_magic(".txt", b"anything at all") is True


def test_unsupported_extension_is_rejected(client, sample_txt):
    response = client.post(
        "/corpora/import",
        files={"source": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert "不支持的文件类型" in response.text


def test_container_file_with_mismatched_content_is_rejected(client):
    response = client.post(
        "/corpora/import",
        files={"source": ("book.docx", b"not really a docx", "application/octet-stream")},
        headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]},
    )

    assert response.status_code == 415
    assert "不符" in response.text


def test_oversized_upload_is_rejected(web_service, sample_txt):
    from fastapi.testclient import TestClient

    from app.pipeline.service import ReadingStudioService
    from app.web.app import create_app
    from tests.web.conftest import bootstrap_csrf

    service = ReadingStudioService(
        web_service.config.model_copy(update={"web_max_upload_bytes": 2_048})
    )
    with TestClient(create_app(config=service.config, service=service)) as client:
        token = bootstrap_csrf(client)

        response = client.post(
            "/corpora/import",
            files={"source": ("big.txt", b"x" * 5_000, "text/plain")},
            headers={"X-CSRF-Token": token},
        )

    assert response.status_code == 413
    assert "MB" in response.text


def test_export_download_refuses_traversal_and_absolute_paths(client, web_service):
    exports = web_service.config.output_dir / "exports" / "manual-test"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "ok.json").write_text("{}", encoding="utf-8")

    assert client.get("/exports/file/manual-test/ok.json").status_code == 200
    for attempt in (
        "/exports/file/../state.db",
        "/exports/file/..%2F..%2Fstate.db",
        "/exports/file//etc/passwd",
        "/exports/file/manual-test/../../../etc/passwd",
    ):
        response = client.get(attempt)
        assert response.status_code in {403, 404}, attempt


def test_export_download_refuses_a_symlink_escape(client, web_service, tmp_path):
    exports = web_service.config.output_dir / "exports" / "manual-test"
    exports.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "outside.txt"
    secret.write_text("private", encoding="utf-8")
    link = exports / "escape.txt"
    try:
        os.symlink(secret, link)
    except OSError:  # pragma: no cover - platform without symlink support
        return

    response = client.get("/exports/file/manual-test/escape.txt")

    assert response.status_code in {403, 404}
    assert "private" not in response.text


def test_novel_file_endpoint_refuses_traversal(client, web_service):
    from app.web import novel_library as library

    digest = "ab" * 32
    source = web_service.config.base_dir / "book.txt"
    source.write_text("第一章 起程\n" + "内容。" * 200, encoding="utf-8")
    entry = library.register_book(
        web_service,
        digest=digest,
        filename="book.txt",
        extension=".txt",
        payload_path=source,
        chapters=1,
        confident=True,
        bytes_written=source.stat().st_size,
    )
    output = library.book_paths(web_service, entry["id"], ".txt").output
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    assert client.get(f"/novel/files/index.html?book={entry['id']}").status_code == 200
    for attempt in (
        f"/novel/files/../state.db?book={entry['id']}",
        f"/novel/files/..%2Fstate.db?book={entry['id']}",
        f"/novel/files//etc/passwd?book={entry['id']}",
    ):
        assert client.get(attempt).status_code in {403, 404}, attempt
    # 换成一本不存在的书也不能借路读到别人的成品
    assert client.get("/novel/files/index.html?book=ffffffffffff").status_code == 404


def test_export_bundle_name_cannot_escape_the_exports_root(client, web_service):
    exports = web_service.config.output_dir / "exports" / "manual-test"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "one.json").write_text("{}", encoding="utf-8")

    assert client.get("/exports/bundle/manual-test").status_code == 200
    for attempt in ("..%2F..%2F", "%2e%2e%2f%2e%2e%2f", "..%2Foutput", "%2Fetc%2Fpasswd"):
        assert client.get(f"/exports/bundle/{attempt}").status_code == 404, attempt
