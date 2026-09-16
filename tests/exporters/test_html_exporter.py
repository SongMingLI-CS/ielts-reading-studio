from app.exporters.html_exporter import export_html


def test_html_hides_answers_until_submission(valid_package, tmp_path):
    html = export_html(valid_package, tmp_path / "practice.html").read_text(encoding="utf-8")
    assert 'data-answer="' not in html
    assert 'type="application/json"' in html
    assert "查看解析" in html
    assert "submit-practice" in html
    assert "evidence-panel" in html


def test_html_escapes_model_supplied_markup(valid_package, tmp_path):
    valid_package.passage.paragraphs[0].text += '<script>alert("x")</script>'
    html = export_html(valid_package, tmp_path / "practice.html").read_text(encoding="utf-8")
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


def test_html_is_standalone(valid_package, tmp_path):
    html = export_html(valid_package, tmp_path / "practice.html").read_text(encoding="utf-8")
    assert "<style>" in html
    assert "<script>" in html
    assert 'src="' not in html
    assert 'href="http' not in html
