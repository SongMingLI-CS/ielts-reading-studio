"""Production integration tests: isolated databases, unchanged scoring rules."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import QuestionGroup, ReadingPassage
from app.web import routes_practice as routes


def start(client, unit, ident='workspace', mode='study', minutes=20):
    r = client.post(f'/practice/{unit.id}/start', json={'attempt_id': ident, 'mode': mode, 'minutes': minutes})
    assert r.status_code == 200
    return r.json()


def save(client, unit, state, answers, **extra):
    return client.post(f'/practice/{unit.id}/save', json={'attempt_id': state['attempt_id'], 'revision': state['revision'], 'answers': answers, **extra})


def test_server_draft_restores_answers_ui_and_elapsed(client, completed_unit):
    state = start(client, completed_unit)
    ui = {'current': 3, 'flags': [3], 'pane': 'questions', 'text_size': 20,
          'highlights': {'paragraph-A': [[0, 5]]}, 'reading_anchor': 'paragraph-B', 'reading_offset': -20,
          'question_anchor': 'question-3', 'question_offset': 12}
    r = save(client, completed_unit, state, {'1': 'water supply'}, ui=ui, elapsed_seconds=80)
    assert r.status_code == 200 and r.json()['revision'] == 2
    resumed = client.get(f'/practice/{completed_unit.id}/state/workspace').json()
    assert resumed['answers'] == {'1': 'water supply'}
    assert resumed['ui']['highlights'] == ui['highlights']
    assert resumed['ui']['flags'] == [3] and resumed['elapsed_seconds'] == 80
    html = client.get(f'/practice/{completed_unit.id}?attempt=workspace').text
    assert '"current": 3' in html and '"pane": "questions"' in html


def test_submission_terminal_idempotent_and_review_connected(client, completed_unit):
    state = start(client, completed_unit)
    path = f'/practice/{completed_unit.id}'
    data = {'attempt_id': 'workspace', 'revision': state['revision'], 'answers': {'1': 'water supply'}}
    first = client.post(path+'/submit', json=data)
    assert first.status_code == 200 and first.json()['correct'] == 1
    again = client.post(path+'/submit', json={**data, 'answers': {}})
    assert again.json() == first.json()
    assert save(client, completed_unit, state, {}).status_code == 409
    row = client.app.state.service.repository.get_practice_attempt('workspace')
    assert row['status'] == 'submitted' and row['score'] == 1
    assert client.get(path+'?attempt=workspace', follow_redirects=False).status_code == 303
    assert len(client.app.state.service.repository.list_practice_attempts(completed_unit.id)) == 1
    assert len(routes.mistake_items(client.app.state.service)) == 11
    assert '原文给出了答案。' in client.get(first.json()['redirect_url']).text


def test_two_tabs_stale_revision_cannot_overwrite(client, completed_unit):
    state = start(client, completed_unit)
    assert save(client, completed_unit, state, {'1': 'first'}).status_code == 200
    conflict = save(client, completed_unit, state, {'1': 'second'})
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'revision_conflict'
    assert client.get(f'/practice/{completed_unit.id}/state/workspace').json()['answers'] == {'1': 'first'}
    stale_submit = client.post(f'/practice/{completed_unit.id}/submit', json={'attempt_id': 'workspace', 'revision': state['revision'], 'answers': {}})
    assert stale_submit.status_code == 409


def test_concurrent_workers_accept_only_one_revision(client, completed_unit):
    state = start(client, completed_unit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda answer: save(client, completed_unit, state, {'1': answer}), ['one', 'two']))
    assert sorted(r.status_code for r in responses) == [200, 409]


@pytest.mark.parametrize('action', ['state', 'save', 'submit', 'page'])
def test_deadline_authoritative_and_late_answers_ignored(client, completed_unit, monkeypatch, action):
    state = start(client, completed_unit, mode='exam', minutes=1)
    saved = save(client, completed_unit, state, {'1': 'water supply'}, elapsed_seconds=9999).json()
    # Server time is isolated from authentication's clock and no real sleeps needed.
    monkeypatch.setattr(routes, 'time', SimpleNamespace(time=lambda: state['deadline'] + 2))
    path = f'/practice/{completed_unit.id}'
    if action == 'state': r = client.get(path+'/state/workspace')
    elif action == 'page': r = client.get(path+'?attempt=workspace', follow_redirects=False)
    else: r = client.post(path+'/'+action, json={'attempt_id':'workspace','revision':saved['revision'],'answers':{'1':'wrong','2':'water supply'}})
    assert r.status_code in [200, 303]
    attempt = client.app.state.service.repository.get_practice_attempt('workspace')
    assert attempt['status'] == 'submitted' and attempt['score'] == 1
    assert attempt['payload']['answers'] == {'1':'water supply'}
    assert attempt['payload']['elapsed_seconds'] == 60 and attempt['payload']['timed_out']


def test_exam_refresh_does_not_restart_or_leak(client, completed_unit):
    state = start(client, completed_unit, mode='exam')
    restarted = start(client, completed_unit, mode='exam', minutes=60)
    assert restarted['deadline'] == state['deadline']
    path=f'/practice/{completed_unit.id}'
    html=client.get(path+'?mode=exam').text
    assert '单篇限时训练' in html and '原文给出了答案。' not in html
    assert '"answer":' not in html and 'chinese_explanation' not in html
    assert client.get(path+'/analysis').status_code == 409
    assert client.get(path+'/analysis?attempt=workspace').status_code == 409
    assert client.get(path+'/result/workspace').status_code == 409
    assert client.get(path+'/state/workspace').json()['deadline'] == state['deadline']


def test_study_has_no_deadline_and_legacy_resume(client, completed_unit):
    service=client.app.state.service
    service.repository.save_practice_attempt('legacy', completed_unit.id, status='in_progress', payload={'answers':{'1':'water'},'elapsed_seconds':42})
    page=client.get(f'/practice/{completed_unit.id}')
    assert 'data-attempt-id="legacy"' in page.text
    r=client.post(f'/practice/{completed_unit.id}/save',json={'attempt_id':'legacy','answers':{'1':'water supply'}})
    assert r.status_code == 200 and r.json()['deadline'] is None
    assert save(client, completed_unit, {'attempt_id':'legacy','revision':r.json()['revision']}, {}).status_code == 200


@pytest.mark.parametrize('kind', ['matching_headings','matching_information','multiple_choice','true_false_not_given','yes_no_not_given','sentence_completion','summary_completion','short_answer'])
def test_each_supported_type_uses_real_scorer_and_widget(client, completed_unit, kind):
    fixture=json.loads((Path(__file__).parents[1]/'fixtures/reading/workspace_types.json').read_text())
    group=next(QuestionGroup.model_validate(g) for g in fixture['question_groups'] if g['type']==kind)
    group.questions[0].number = 1
    package=completed_unit.package.model_copy(update={'passage':ReadingPassage.model_validate(fixture['passage']), 'question_groups':[group]})
    client.app.state.service.store.write_package(completed_unit.id, package)
    page=client.get(f'/practice/{completed_unit.id}').text
    q=group.questions[0]
    assert f'data-group="{kind}"' in page and 'chinese_explanation' not in page
    if kind in ['matching_headings','matching_information']: assert '<select aria-labelledby=' in page
    elif kind in ['multiple_choice','true_false_not_given','yes_no_not_given']: assert '<fieldset class="choices' in page
    else: assert 'data-word-limit=' in page
    r=client.post(f'/practice/{completed_unit.id}/submit',json={'attempt_id':kind,'answers':{str(q.number):q.answer}})
    assert r.status_code == 200 and r.json()['correct']==1
    assert r.json()['results'][0]['evidence_quote']==q.evidence_quote
    bad=client.post(f'/practice/{completed_unit.id}/submit',json={'attempt_id':kind+'-blank','answers':{}})
    assert bad.json()['correct']==0


def test_foreign_attempt_cannot_be_rebound(client, completed_unit, needs_review_unit):
    service=client.app.state.service
    service.repository.save_practice_attempt('foreign',needs_review_unit.id,status='in_progress',payload={})
    assert client.post(f'/practice/{completed_unit.id}/submit',json={'attempt_id':'foreign','answers':{}}).status_code==409
    assert service.repository.get_practice_attempt('foreign')['unit_id']==needs_review_unit.id


def test_native_form_revision_submit(client, completed_unit):
    state=start(client,completed_unit)
    r=client.post(f'/practice/{completed_unit.id}/submit',data={'attempt_id':'workspace','revision':state['revision'],'q1':'water supply'},follow_redirects=False)
    assert r.status_code==303


def test_multiple_choice_existing_list_scoring_and_form_controls(client, completed_unit):
    # Existing scorer accepts a list against semicolon-separated full options.
    options=['A. First reason','B. Second reason','C. Third reason']
    group=QuestionGroup.model_validate({'type':'multiple_choice','instructions':'Choose TWO answers.', 'options':options,'questions':[{'number':1,'prompt':'Which two reasons?','answer':'A. First reason; C. Third reason','evidence_paragraph':'A','evidence_quote':'Clean water','chinese_explanation':'Existing list grading.'}]})
    package=completed_unit.package.model_copy(update={'question_groups':[group]})
    client.app.state.service.store.write_package(completed_unit.id,package)
    html=client.get(f'/practice/{completed_unit.id}').text
    assert html.count('type="checkbox"')==3
    state=start(client,completed_unit)
    values=[('attempt_id','workspace'),('revision',str(state['revision'])),('q1',options[0]),('q1',options[2])]
    from urllib.parse import urlencode
    r=client.post(f'/practice/{completed_unit.id}/submit',content=urlencode(values),headers={'Content-Type':'application/x-www-form-urlencoded'},follow_redirects=False)
    assert r.status_code==303
    attempt=client.app.state.service.repository.get_practice_attempt('workspace')
    assert attempt['score']==1 and attempt['payload']['answers']=={'1':[options[0],options[2]]}


def test_library_continue_resumes_latest_exam_mode(client, completed_unit):
    start(client,completed_unit,mode='exam')
    html=client.get(f'/practice/{completed_unit.id}').text
    assert 'data-mode="exam"' in html and 'data-attempt-id="workspace"' in html
    response=client.get(f'/practice/{completed_unit.id}/state/workspace')
    assert response.headers['cache-control']=='no-store'


def test_native_form_has_stable_id_and_cannot_fake_exam(client, completed_unit):
    import re
    html=client.get(f'/practice/{completed_unit.id}?fresh=1').text
    ident=re.search(r'name="attempt_id" value="([^"]+)"',html).group(1)
    data={'attempt_id':ident,'mode':'study','q1':'water supply'}
    first=client.post(f'/practice/{completed_unit.id}/submit',data=data,follow_redirects=False)
    second=client.post(f'/practice/{completed_unit.id}/submit',data={**data,'q1':'wrong'},follow_redirects=False)
    assert first.headers['location']==second.headers['location']
    assert client.app.state.service.repository.get_practice_attempt(ident)['score']==1
    assert client.post(f'/practice/{completed_unit.id}/submit',data={'attempt_id':'not-started','mode':'exam'},follow_redirects=False).status_code==409


def test_redo_links_start_fresh_study_without_overwriting_draft_or_history(client, completed_unit):
    from bs4 import BeautifulSoup

    old = start(client, completed_unit, ident='old-draft')
    saved = save(client, completed_unit, old, {'1': 'keep my draft'}, ui={'current': 6}).json()
    client.post(f'/practice/{completed_unit.id}/submit', json={'attempt_id': 'history', 'answers': {}})
    repository = client.app.state.service.repository
    before = repository.list_practice_attempts(completed_unit.id)
    mistakes_before = routes.mistake_items(client.app.state.service)
    page = BeautifulSoup(client.get('/practice/review').text, 'html.parser')
    redo = page.find('a', string='重新练习这一篇')['href']
    assert redo == f'/practice/{completed_unit.id}?fresh=1&mode=study'
    fresh_page = BeautifulSoup(client.get(redo).text, 'html.parser')
    assert json.loads(fresh_page.select_one('#session-state').text) is None
    new_id = fresh_page.select_one('[name=attempt_id]')['value']
    assert new_id not in ('old-draft', 'history')
    new = start(client, completed_unit, ident=new_id)
    assert new['answers'] == {} and new['mode'] == 'study'
    assert repository.get_practice_attempt('old-draft')['payload']['answers'] == {'1': 'keep my draft'}
    assert repository.get_practice_attempt('old-draft')['payload']['revision'] == saved['revision']
    assert repository.get_practice_attempt('history') == next(a for a in before if a['id'] == 'history')
    assert routes.mistake_items(client.app.state.service) == mistakes_before
    resumed = client.get(f'/practice/{completed_unit.id}?attempt=old-draft').text
    assert 'keep my draft' in resumed and '"current": 6' in resumed


def test_fresh_study_does_not_resume_active_exam(client, completed_unit):
    exam = start(client, completed_unit, ident='exam-existing', mode='exam')
    page = client.get(f'/practice/{completed_unit.id}?fresh=1&mode=study').text
    assert 'data-mode="study"' in page
    assert '<script id="session-state" type="application/json">null</script>' in page
    assert client.get(f'/practice/{completed_unit.id}/state/exam-existing').json()['deadline'] == exam['deadline']


def test_history_analysis_409_still_enforces_exam_rule_and_has_recovery_ui(client, completed_unit):
    from bs4 import BeautifulSoup

    submitted = client.post(f'/practice/{completed_unit.id}/submit', json={'attempt_id': 'history', 'answers': {}}).json()
    exam = start(client, completed_unit, ident='active-exam', mode='exam')
    page = BeautifulSoup(client.get(submitted['redirect_url']).text, 'html.parser')
    link = page.select_one('[data-analysis-link]')
    blocked = client.get(link['href'], headers={'Accept': 'application/json'})
    assert blocked.status_code == 409
    assert blocked.json()['detail'] == '考试进行中，交卷后才能查看解析'
    assert link['data-exam-url'] == f'/practice/{completed_unit.id}?mode=exam'
    assert page.select_one('#analysis-dialog')['aria-describedby'] == 'analysis-message'
    assert page.select_one('#analysis-message')['aria-live'] == 'polite'
    assert page.select_one('#analysis-retry') is not None
    assert client.get(f'/practice/{completed_unit.id}/result/active-exam').status_code == 409
    finished = client.post(f'/practice/{completed_unit.id}/submit', json={'attempt_id': 'active-exam', 'revision': exam['revision'], 'answers': {}})
    assert finished.status_code == 200
    assert client.get(link['href']).status_code == 200
    assert client.get(submitted['redirect_url']).status_code == 200


def test_result_redo_preserves_requested_mode(client, completed_unit):
    from bs4 import BeautifulSoup

    exam = start(client, completed_unit, mode='exam')
    result = client.post(f'/practice/{completed_unit.id}/submit', json={'attempt_id': exam['attempt_id'], 'revision': exam['revision'], 'answers': {}}).json()
    page = BeautifulSoup(client.get(result['redirect_url']).text, 'html.parser')
    assert page.find('a', string='重新练习')['href'] == f'/practice/{completed_unit.id}?fresh=1&mode=exam'
    assert all(b['aria-label'].startswith('第 ') for b in page.select('[data-result]'))
