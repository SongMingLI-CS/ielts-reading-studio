from app.exporters.json_exporter import export_json
from app.models import ReadingPackage


def test_json_round_trip_is_canonical(valid_package, tmp_path):
    path = export_json(valid_package, tmp_path / "package.json")
    assert ReadingPackage.model_validate_json(path.read_text(encoding="utf-8")) == valid_package
    assert path.read_bytes().endswith(b"\n")


def test_json_rejects_unvalidated_package(valid_package, tmp_path):
    valid_package.quality_report.passed = False
    try:
        export_json(valid_package, tmp_path / "package.json")
    except ValueError as exc:
        assert "validated" in str(exc)
    else:
        raise AssertionError("unvalidated package was exported")
