(() => {
  'use strict';
  document.querySelectorAll('form[action="/learn/import"], form[action="/learn/upload"], form[action="/learn/collection"]').forEach(form => {
    form.addEventListener('submit', event => {
      const file = form.querySelector('[type="file"]')?.files[0];
      const maximum = Number(form.dataset.maxUploadBytes);
      const status = form.querySelector('.learn-form-status');
      if (file && maximum && file.size > maximum) {
        event.preventDefault();
        status.textContent = `文件超过 ${Math.floor(maximum / 1048576)} MB，请选择更小的文件。`;
        return;
      }
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      const collection = form.action.endsWith('/learn/collection');
      button.textContent = collection ? '正在导入整套章节…' : '正在保存原文…';
      if (status) status.textContent = collection ? '正在下载多篇官方文档，完成后自动打开。请保持页面开启。' : '正在保存完整正文，完成后自动打开。';
      form.setAttribute('aria-busy', 'true');
    });
  });
  const reader = document.querySelector('[data-learning-reader]');
  if (!reader) return;
  const progress = JSON.parse(document.getElementById('learning-progress-data').textContent);
  let guide = JSON.parse(document.getElementById('learning-guide-data').textContent);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  async function post(path, data, sectionId = reader.dataset.section) {
    const response = await fetch(`/api/learn/${reader.dataset.document}/${sectionId}` + path, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.IELTS_CSRF?.token || ''}, body: JSON.stringify(data || {}), signal: AbortSignal.timeout(180000)});
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
  reader.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-code]');
    if (!button) return;
    try { await navigator.clipboard.writeText(button.closest('.learn-code').querySelector('code').textContent); button.textContent = '已复制 ✓'; }
    catch { button.textContent = '请选中代码复制'; }
  });
  const popup = reader.querySelector('#reading-word-popover');
  const toggleGlosses = reader.querySelector('[data-toggle-glosses]');
  let activeWord = null;
  function closeWord(restoreFocus = false) {
    popup.hidden = true;
    if (activeWord) {
      activeWord.setAttribute('aria-expanded', 'false');
      if (restoreFocus && activeWord.isConnected) activeWord.focus();
    }
    activeWord = null;
  }
  function setGlosses(enabled) {
    reader.classList.toggle('learn-glosses-off', !enabled);
    toggleGlosses.setAttribute('aria-pressed', String(enabled));
    toggleGlosses.textContent = enabled ? '难词提示：开启' : '难词提示：关闭';
    reader.querySelectorAll('[data-reading-word]').forEach(button => { button.disabled = !enabled; });
    if (!enabled) closeWord();
  }
  try { setGlosses(localStorage.getItem('learning-word-hints') !== 'off'); } catch { setGlosses(true); }
  toggleGlosses.addEventListener('click', () => {
    const enabled = toggleGlosses.getAttribute('aria-pressed') !== 'true';
    setGlosses(enabled);
    try { localStorage.setItem('learning-word-hints', enabled ? 'on' : 'off'); } catch { /* Reading works without storage. */ }
  });
  reader.addEventListener('click', event => {
    const button = event.target.closest('[data-reading-word]');
    if (!button || button.disabled) return;
    if (activeWord === button && !popup.hidden) { closeWord(true); return; }
    closeWord();
    activeWord = button;
    popup.querySelector('#reading-word-heading').textContent = button.dataset.readingWord;
    popup.querySelector('[data-word-source]').textContent = button.dataset.meaningSource;
    popup.querySelector('[data-word-meaning]').textContent = button.dataset.chinese;
    popup.querySelector('[data-word-note]').textContent = button.dataset.usageNote || '这是常用释义；可补充本节语境释义，查看它在原句中的具体用法。';
    const quote = popup.querySelector('[data-word-quote]');
    quote.textContent = button.dataset.sourceQuote || '';
    quote.hidden = !quote.textContent;
    popup.hidden = false;
    const rect = button.getBoundingClientRect();
    popup.style.left = `${Math.max(16, Math.min(rect.left, window.innerWidth - popup.offsetWidth - 16))}px`;
    let top = rect.bottom + 10;
    if (top + popup.offsetHeight > window.innerHeight - 90) top = rect.top - popup.offsetHeight - 10;
    popup.style.top = `${Math.max(96, Math.min(top, window.innerHeight - popup.offsetHeight - 90))}px`;
    button.setAttribute('aria-expanded', 'true');
    popup.querySelector('[data-close-word]').focus();
  });
  popup.querySelector('[data-close-word]').addEventListener('click', () => closeWord(true));
  document.addEventListener('click', event => {
    if (!popup.hidden && !popup.contains(event.target) && !event.target.closest('[data-reading-word]')) closeWord();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !popup.hidden) { event.preventDefault(); closeWord(true); } });
  window.addEventListener('resize', () => closeWord());
  reader.querySelectorAll('[data-reading-vocabulary]').forEach(button => button.addEventListener('click', () => action(button, button.parentElement.querySelector('[data-vocabulary-status]'), async () => {
    const status = button.parentElement.querySelector('[data-vocabulary-status]');
    status.textContent = '正在结合本节原句整理中文释义与英语用法…';
    await post('/vocabulary', {}, button.dataset.section);
    const response = await fetch(`/learn/${reader.dataset.document}?section=${button.dataset.section}`, {credentials:'same-origin',signal:AbortSignal.timeout(30000)});
    if (!response.ok) throw new Error('释义已保存，刷新页面即可查看更新后的标注。');
    const updated = new DOMParser().parseFromString(await response.text(), 'text/html');
    const original = button.closest('[data-learning-section]');
    const source = updated.querySelector('.learn-source-text');
    if (!source) throw new Error('释义已保存，刷新页面即可查看更新后的标注。');
    closeWord();
    original.querySelector('.learn-source-text').replaceWith(document.importNode(source, true));
    const oldList = original.querySelector('.learn-reading-glossary');
    const newList = updated.querySelector('.learn-reading-glossary');
    if (newList) {
      const list = document.importNode(newList, true);
      list.open = true;
      if (oldList) oldList.replaceWith(list); else button.before(list);
    }
    setGlosses(toggleGlosses.getAttribute('aria-pressed') === 'true');
    status.textContent = '语境释义已保存，原文中的词语标注已更新。';
    button.textContent = '查看已保存语境释义';
  })));
  reader.querySelectorAll('[data-mark-read]').forEach(mark => mark.addEventListener('click', () => action(mark, mark.parentElement.querySelector('[data-progress-status]'), async () => {
    const completed = mark.getAttribute('aria-pressed') !== 'true';
    await post('/progress', {completed}, mark.dataset.section);
    mark.setAttribute('aria-pressed', String(completed));
    mark.textContent = completed ? '已读完 ✓' : '标记本节已读';
    reader.querySelector(`[data-section-marker="${mark.dataset.section}"]`).textContent = completed ? '✓' : '·';
    mark.parentElement.querySelector('[data-progress-status]').textContent = '阅读进度已保存。';
  })));
  if (reader.dataset.readingMode === 'continuous') return;
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
