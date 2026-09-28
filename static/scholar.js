(() => {
  'use strict';
  const launch = document.querySelector('[data-scholar-launch]');
  const dialog = document.getElementById('scholar-dialog');
  if (!launch || !dialog || typeof dialog.showModal !== 'function') return;
  const form = document.getElementById('scholar-form');
  const status = document.getElementById('scholar-status');
  const result = dialog.querySelector('.scholar-result');
  const submit = form.querySelector('[type="submit"]');
  let selected = '';
  let source = '';
  let busy = false;
  let requestId = null;
  let previousPayload = '';
  launch.hidden = false;
  document.addEventListener('selectionchange', () => {
    if (dialog.open) return;
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    const main = document.getElementById('main');
    if (main && main.contains(range.startContainer) && main.contains(range.endContainer)) selected = selection.toString().trim();
  });
  launch.addEventListener('click', () => {
    form.elements.title.value = (document.querySelector('main h1')?.textContent || document.title).trim().slice(0, 200);
    if (selected) form.elements.text.value = selected;
    source = location.pathname + location.search + location.hash;
    dialog.querySelector('.scholar-source').textContent = '来源：' + location.origin + source;
    status.textContent = '';
    result.hidden = true;
    dialog.showModal();
    (form.elements.text.value ? form.elements.question : form.elements.text).focus();
  });
  dialog.querySelector('.scholar-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => launch.focus());
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const payload = { title: form.elements.title.value.trim(), text: form.elements.text.value.trim(), question: form.elements.question.value.trim(), source_path: source };
    const documentReader = document.querySelector('[data-learning-reader]');
    if (documentReader) {
      payload.document_url = documentReader.dataset.sourceUrl || '';
      payload.document_version = documentReader.dataset.version || '';
    }
    if (!payload.text || payload.text.length > 12000 || !payload.question || payload.question.length > 2000) {
      status.textContent = '请填写原文和问题；原文最多 12,000 字，问题最多 2,000 字。';
      return;
    }
    const signature = JSON.stringify(payload);
    if (!requestId || signature !== previousPayload) requestId = crypto.randomUUID();
    previousPayload = signature;
    busy = true;
    submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    status.textContent = '正在创建研究对话…';
    result.hidden = true;
    try {
      const response = await fetch('/api/scholar/import', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.IELTS_CSRF?.token || '' },
        body: JSON.stringify({ ...payload, request_id: requestId }), signal: AbortSignal.timeout(30000)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '创建失败，请重试。');
      result.href = data.url;
      result.hidden = false;
      status.textContent = '研究对话已创建，原文和问题已保存。点击「打开研究对话」继续。';
      result.focus();
    } catch (error) {
      status.textContent = error.name === 'TimeoutError' ? '连接超时，内容仍保留在这里，请重试。' : error.message || '暂时无法连接，请重试。';
    } finally {
      busy = false;
      submit.disabled = false;
      form.removeAttribute('aria-busy');
    }
  });
})();
