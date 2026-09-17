import sqlite3
import zipfile


def test_backup_page_reports_what_would_be_archived(client, web_service, sample_txt):
    web_service.import_source(sample_txt)
    page = client.get("/backup")
    assert page.status_code == 200
    assert "备份与恢复" in page.text
    assert "不含密钥" in page.text
    assert "state.db" in page.text
    assert str(web_service.config.output_dir) in page.text


def test_backup_zip_contains_the_snapshot_and_never_the_secrets(
    client, web_service, sample_txt, tmp_path
):
    web_service.import_source(sample_txt)
    # a secret next to the project is exactly what must not travel
    (web_service.config.base_dir / ".env").write_text(
        "DEEPSEEK_API_KEY=sk-sentinel-value\n", encoding="utf-8"
    )

    response = client.get("/backup/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]

    archive_path = tmp_path / "backup.zip"
    archive_path.write_bytes(response.content)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "state.db" in names
        assert "BACKUP-README.txt" in names
        assert any(name.startswith("output/") for name in names)
        assert not any(".env" in name for name in names)
        assert b"sk-sentinel-value" not in response.content
        # the snapshot is a readable SQLite database
        snapshot = tmp_path / "state.db"
        snapshot.write_bytes(archive.read("state.db"))
    connection = sqlite3.connect(snapshot)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()
    assert "corpora" in tables and "generation_units" in tables
