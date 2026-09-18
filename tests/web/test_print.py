def test_print_pages_use_the_standalone_print_stylesheet(client):
    mistakes = client.get("/practice/mistakes/print")
    vocabulary = client.get("/practice/vocabulary/print")

    for page in (mistakes, vocabulary):
        assert page.status_code == 200
        assert "/static/print.css" in page.text
        assert "打印 / 存为 PDF" in page.text
        # 打印页不套站点外壳，页面上没有主导航
        assert 'class="site-header"' not in page.text
        assert "← 练习中心" not in page.text


def _submit_a_wrong_answer(client, completed_unit):
    response = client.post(
        f"/practice/{completed_unit.id}/submit",
        json={"attempt_id": "attempt-print", "answers": {"1": "definitely wrong"}},
    )
    assert response.status_code == 200
    return "attempt-print"


def test_print_mistakes_includes_and_hides_explanations(client, completed_unit):
    _submit_a_wrong_answer(client, completed_unit)

    with_explanations = client.get("/practice/mistakes/print?explanations=1")
    without = client.get("/practice/mistakes/print?explanations=0")

    assert with_explanations.status_code == without.status_code == 200
    assert "含解析" in with_explanations.text and "不含解析" in without.text
    assert "你的答案" in with_explanations.text and "正确答案" in with_explanations.text
    assert with_explanations.text.count('class="explain"') >= 1
    assert without.text.count('class="explain"') == 0


def test_print_mistakes_shows_an_empty_state_without_attempts(client):
    page = client.get("/practice/mistakes/print")

    assert page.status_code == 200
    assert "还没有错题记录" in page.text


def test_print_vocabulary_list_and_quiz_modes(client, web_service, vocabulary_unit):
    web_service.repository.set_vocabulary_mark("conservation", "saved")

    listing = client.get("/practice/vocabulary/print?scope=saved&mode=list")
    quiz = client.get("/practice/vocabulary/print?scope=saved&mode=quiz")

    assert listing.status_code == quiz.status_code == 200
    assert "conservation" in listing.text
    assert "保护；节约" in listing.text
    # 自测卷：只留横线 + 末尾答案
    assert quiz.text.count("quiz-line") >= 1
    assert "答案" in quiz.text


def test_print_vocabulary_reports_an_empty_scope(client):
    page = client.get("/practice/vocabulary/print?scope=known")

    assert page.status_code == 200
    assert "这个范围里还没有单词" in page.text
