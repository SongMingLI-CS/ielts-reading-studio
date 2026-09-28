(() => {
  'use strict';
  document.querySelectorAll('form[action="/learn/import"], form[action="/learn/upload"]').forEach(form => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      button.textContent = '正在保存原文…';
      form.setAttribute('aria-busy', 'true');
    });
  });
  const reader = document.querySelector('[data-learning-reader]');
  if (!reader) return;
  const api = `/api/learn/${reader.dataset.document}/${reader.dataset.section}`;
  const progress = JSON.parse(document.getElementById('learning-progress-data').textContent);
  let guide = JSON.parse(document.getElementById('learning-guide-data').textContent);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  async function post(path, data) {
    const response = await fetch(api + path, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.IELTS_CSRF?.token || ''}, body: JSON.stringify(data || {}), signal: AbortSignal.timeout(180000)});
    let payload;
    try { payload = await response.json(); } catch { throw new Error('服务暂时无法返回结果，请重试。'); }
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '请求未完成，请检查输入后重试。');
    return payload;
  }
  async function action(button, status, work) {
    button.disabled = true;
    status.textContent = '正在保存…';
    try { await work(); } catch (error) { status.textContent = error.name === 'TimeoutError' ? '请求超时，请稍后重试。' : error.message; }
    finally { button.disabled = false; }
  }
  reader.querySelectorAll('[data-copy-code]').forEach(button => button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.closest('.learn-code').querySelector('code').textContent); button.textContent = '已复制 ✓'; }
    catch { button.textContent = '请选中代码复制'; }
  }));
  const mark = reader.querySelector('[data-mark-read]');
  mark.addEventListener('click', () => action(mark, document.getElementById('learning-progress-status'), async () => {
    const completed = mark.getAttribute('aria-pressed') !== 'true';
    await post('/progress', {completed});
    mark.setAttribute('aria-pressed', String(completed));
    mark.textContent = completed ? '已读完 ✓' : '标记本节已读';
    reader.querySelector(`[data-section-marker="${reader.dataset.section}"]`).textContent = completed ? '✓' : '·';
    document.getElementById('learning-progress-status').textContent = '阅读进度已保存。';
  }));
  const save = reader.querySelector('[data-save-notes]');
  save.addEventListener('click', () => action(save, document.getElementById('learning-notes-status'), async () => {
    await post('/progress', {notes: document.getElementById('learning-notes').value});
    document.getElementById('learning-notes-status').textContent = '笔记已保存。';
  }));
  function renderGuide() {
    const target = document.getElementById('learning-guide-content');
    target.replaceChildren();
    if (!guide) return;
    target.append(element('h3', '英语表达'));
    const words = element('div', undefined, 'learn-word-grid');
    guide.glossary.forEach(word => {
      const card = element('article', undefined, 'learn-word');
      card.append(element('h4', word.term), element('p', word.chinese), element('p', word.english_explanation, 'learn-english'));
      words.append(card);
    });
    target.append(words, element('h3', '关键概念'));
    guide.concepts.forEach(concept => {
      const card = element('article', undefined, 'learn-concept');
      card.append(element('h4', concept.name), element('p', concept.explanation));
      target.append(card);
    });
    target.append(element('h3', '确认你读懂了'));
    const form = element('form');
    guide.questions.forEach((question, index) => {
      const field = element('fieldset', undefined, 'learn-question');
      field.append(element('legend', `${index + 1}. ${question.prompt}`), element('p', question.kind === 'english' ? '英语理解' : '概念自测', 'learn-question-kind'));
      question.choices.forEach((choice, option) => {
        const label = element('label');
        const input = element('input');
        input.type = 'radio'; input.name = `q${index}`; input.value = String(option); input.required = true;
        if (progress.answers?.[index] === option) input.checked = true;
        label.append(input, element('span', choice)); field.append(label);
      });
      form.append(field);
    });
    const submit = element('button', '检查答案'); submit.type = 'submit';
    const status = element('p'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    if (progress.score !== undefined) status.textContent = `上次自测：${progress.score} / ${progress.total}`;
    form.append(submit, status);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      action(submit, status, async () => {
        const answers = guide.questions.map((_, index) => Number(form.querySelector(`input[name="q${index}"]:checked`).value));
        const result = await post('/quiz', {answers});
        progress.answers = answers; progress.score = result.score; progress.total = result.total;
        status.textContent = `本次自测：${result.score} / ${result.total}，结果已保存。`;
        form.querySelectorAll('.learn-feedback').forEach(node => node.remove());
        result.feedback.forEach((question, index) => {
          const feedback = element('div', undefined, 'learn-feedback');
          feedback.append(element('strong', answers[index] === question.correct_index ? '回答正确' : `正确答案：${question.choices[question.correct_index]}`), element('p', question.explanation), element('blockquote', question.evidence_quote));
          form.querySelectorAll('fieldset')[index].append(feedback);
        });
      });
    });
    target.append(form);
  }
  const generate = reader.querySelector('[data-generate-guide]');
  generate.addEventListener('click', () => action(generate, document.getElementById('learning-guide-status'), async () => {
    const status = document.getElementById('learning-guide-status');
    status.textContent = guide ? '正在读取保存结果…' : '正在结合本节原文生成讲解与自测，请稍候…';
    if (!guide) guide = await post('/guide');
    renderGuide();
    status.textContent = '讲解已保存；下次打开本节会自动显示。';
    generate.textContent = '查看已保存讲解';
  }));
  renderGuide();
})();
