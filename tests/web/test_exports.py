from __future__ import annotations

import pytest

from app.models import QualityReport, UnitStatus


@pytest.fixture
def failed_quality_unit(web_service, completed_unit):
    """A completed unit whose stored package failed the quality gate."""
    package = completed_unit.package.model_copy(deep=True)
    unit = package.unit.model_copy(
        update={"id": "failed-quality", "ordinal": 42, "status": UnitStatus.COMPLETED}
    )
    web_service.repository.add_unit(unit)
    web_service.store.write_package(
        unit.id,
        package.model_copy(
            update={"unit": unit, "quality_report": QualityReport(passed=False)}
        ),
    )
    return unit


def test_export_center_refuses_unvalidated_package(client, needs_review_unit):
    response = client.post(
        "/exports",
        data={"unit_ids": needs_review_unit.id, "format": "docx", "workbook_size": 20},
    )
    assert response.status_code == 409


def test_export_center_creates_completed_unit_export(client, completed_unit, web_service):
    response = client.post(
        "/exports",
        data={"unit_ids": completed_unit.id, "format": "docx", "workbook_size": 20},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert list(web_service.config.output_dir.rglob("*.docx"))


def test_export_center_explains_formats_and_lists_titles(client, completed_unit):
    response = client.get("/exports")

    assert response.status_code == 200
    page = response.text
    # 名词解释：三种格式各自干什么
    assert "HTML 单文件练习页" in page
    assert "DOCX 文档 / 练习册" in page
    assert "JSON 规范数据" in page
    # 列表显示文章标题而不是裸 ID
    title = completed_unit.package.passage.title
    assert title in page
    assert "可导出" in page


def test_export_center_lists_blocked_unit_separately(
    client, completed_unit, web_service, failed_quality_unit
):
    page = client.get("/exports").text
    exportable, _, skipped = page.partition("篇被跳过")

    assert "篇被跳过（质检未通过）" in page
    assert f'value="{failed_quality_unit.id}"' not in exportable
    assert f'value="{completed_unit.id}"' in exportable
    assert f"#{failed_quality_unit.ordinal} · " in skipped
    assert "/exports/file/" not in page  # 还没导出过，不显示下载区


def test_export_center_downloads_created_html(client, completed_unit):
    response = client.post(
        "/exports",
        data={"unit_ids": completed_unit.id, "format": "html", "workbook_size": 20},
        follow_redirects=False,
    )
    assert response.status_code == 303
    batch = response.headers["location"].split("created=")[1].split(":")[0]

    listing = client.get("/exports").text
    assert f"/exports/file/{batch}/" in listing
    assert f"/exports/bundle/{batch}" in listing

    relative = listing.split(f"/exports/file/{batch}/")[1].split('"')[0]
    download = client.get(f"/exports/file/{batch}/{relative}")
    assert download.status_code == 200
    assert "text/html" in download.headers["content-type"]
    assert completed_unit.package.passage.title in download.text

    bundle = client.get(f"/exports/bundle/{batch}")
    assert bundle.status_code == 200
    assert bundle.content[:2] == b"PK"
    assert f"{batch}.zip" in bundle.headers["content-disposition"]


def test_export_download_refuses_traversal_and_unknown_paths(client, completed_unit):
    assert client.get("/exports/file/..%2F..%2Fconfig.yaml").status_code == 404
    assert client.get("/exports/file/missing-batch%2Fnope.html").status_code == 404
    assert client.get("/exports/bundle/..%2F..").status_code == 404
    assert client.get("/exports/bundle/never-existed").status_code == 404


def test_export_center_empty_state_is_self_explanatory(client):
    page = client.get("/exports").text

    assert "还没有可导出的篇目" in page
    assert "还没有导出过任何文件" in page
