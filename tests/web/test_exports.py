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
