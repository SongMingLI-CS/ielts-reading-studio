(() => {
  const form = document.getElementById('practice-form');
  if (!form) return;

  const unitId = form.dataset.unitId;
  const storageKey = `ielts-reading:${unitId}`;
  const fields = [...form.querySelectorAll('input[name^="q"],select[name^="q"]')];
  const numbers = [...new Set(fields.map((field) => field.name.replace(/^q/, '')))];
  const byNumber = (number) => fields.filter((field) => field.name === `q${number}`);
  const el = (id) => document.getElementById(id);

  const timer = el('timer'), saveStatus = el('autosave-status');
  const answeredCount = el('answered-count'), totalCount = el('total-count');
  const progressCopy = el('progress-copy'), progressBar = el('progress-bar');
  const mobileAnsweredCount = el('mobile-answered-count');
  const elapsedInput = el('elapsed-seconds'), submitHint = el('submit-hint');
  const attemptInput = form.querySelector('input[name="attempt_id"]');
  const practiceShell = document.querySelector('.practice-shell');
  const paneResizer = el('pane-resizer');
  const paneTabs = [...document.querySelectorAll('[data-show-pane]')];
  if (practiceShell) practiceShell.classList.add('mobile-tabs-enabled');

  const paneWidthKey = 'ielts-reading:pane-width';
  const defaultPaneWidth = 53;
  const desktopPanes = window.matchMedia('(min-width: 1025px)');
  const clampPaneWidth = (value) => Math.min(70, Math.max(30, value));
  const setPaneWidth = (value, persist = false) => {
    if (!practiceShell || !paneResizer) return;
    const width = clampPaneWidth(Number(value) || defaultPaneWidth);
    practiceShell.style.setProperty('--practice-reading-width', `${width}%`);
    paneResizer.setAttribute('aria-valuenow', String(Math.round(width)));
    if (persist) {
      try { window.localStorage.setItem(paneWidthKey, String(width)); } catch (error) { /* private mode */ }
    }
  };
  if (paneResizer) {
    let savedPaneWidth = defaultPaneWidth;
    try { savedPaneWidth = Number(window.localStorage.getItem(paneWidthKey)) || defaultPaneWidth; } catch (error) { /* private mode */ }
    setPaneWidth(savedPaneWidth);

    const widthFromPointer = (clientX) => {
      const bounds = practiceShell.getBoundingClientRect();
      if (!bounds.width) return defaultPaneWidth;
      return ((clientX - bounds.left) / bounds.width) * 100;
    };
    paneResizer.addEventListener('pointerdown', (event) => {
      if (!desktopPanes.matches || event.button !== 0) return;
      event.preventDefault();
      paneResizer.setPointerCapture(event.pointerId);
      practiceShell.classList.add('is-resizing');
    });
    paneResizer.addEventListener('pointermove', (event) => {
      if (!paneResizer.hasPointerCapture(event.pointerId)) return;
      setPaneWidth(widthFromPointer(event.clientX));
    });
    const finishResize = (event) => {
      if (!paneResizer.hasPointerCapture(event.pointerId)) return;
      paneResizer.releasePointerCapture(event.pointerId);
      practiceShell.classList.remove('is-resizing');
      setPaneWidth(paneResizer.getAttribute('aria-valuenow'), true);
    };
    paneResizer.addEventListener('pointerup', finishResize);
    paneResizer.addEventListener('pointercancel', finishResize);
    paneResizer.addEventListener('lostpointercapture', () => practiceShell.classList.remove('is-resizing'));
    paneResizer.addEventListener('dblclick', () => setPaneWidth(defaultPaneWidth, true));
    paneResizer.addEventListener('keydown', (event) => {
      if (!desktopPanes.matches) return;
      const current = Number(paneResizer.getAttribute('aria-valuenow')) || defaultPaneWidth;
      const step = event.shiftKey ? 5 : 2;
      let next = null;
      if (event.key === 'ArrowLeft') next = current - step;
      if (event.key === 'ArrowRight') next = current + step;
      if (event.key === 'Home') next = defaultPaneWidth;
      if (next === null) return;
      event.preventDefault();
      setPaneWidth(next, true);
    });
  }

  // crypto.randomUUID needs a secure context (https or localhost), so fall back
  // instead of crashing when the page is opened over plain http from a LAN address.
  const newId = () => {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID().replace(/-/g, '');
      }
    } catch (error) { /* fall through to the byte generator */ }
    const bytes = new Uint8Array(16);
    if (window.crypto && typeof window.crypto.getRandomValues === 'function') window.crypto.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
    return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  };

  const readStore = () => { try { return JSON.parse(window.localStorage.getItem(storageKey) || '{}') || {}; } catch (error) { return {}; } };
  const writeStore = (value) => { try { window.localStorage.setItem(storageKey, JSON.stringify(value)); } catch (error) { /* private mode */ } };
  const dropStore = () => { try { window.localStorage.removeItem(storageKey); } catch (error) { /* ignore */ } };
  const parseJson = (text, fallback) => { try { return JSON.parse(text) || fallback; } catch (error) { return fallback; } };

  const serverAnswers = parseJson(form.dataset.savedAnswers || '{}', {});
  const serverSeconds = Number.parseInt(form.dataset.resumeSeconds || '0', 10) || 0;
  const stored = readStore();
  const state = {
    attempt_id: form.dataset.attemptId || stored.attempt_id || newId(),
    answers: { ...serverAnswers },
    elapsed_seconds: Math.max(Number(stored.elapsed_seconds) || 0, serverSeconds),
  };
  for (const [number, value] of Object.entries(stored.answers || {})) {
    if (value) state.answers[number] = value;
  }

  const setStatus = (kind, text) => {
    saveStatus.className = `save-status${kind ? ` ${kind}` : ''}`;
    saveStatus.innerHTML = '<i></i>';
    saveStatus.append(document.createTextNode(` ${text}`));
  };

  const collect = () => {
    const answers = {};
    for (const number of numbers) {
      const group = byNumber(number);
      const selected = group.find((field) => (field.type === 'radio' || field.type === 'checkbox') && field.checked);
      const text = group.find((field) => !['radio', 'checkbox', 'hidden'].includes(field.type));
      const value = selected ? selected.value : (text ? text.value.trim() : '');
      if (value !== '' && value != null) answers[number] = value;
    }
    return answers;
  };

  const hydrate = () => {
    for (const [number, value] of Object.entries(state.answers || {})) {
      for (const field of byNumber(number)) {
        if (field.type === 'radio' || field.type === 'checkbox') field.checked = field.value === value;
        else field.value = value;
      }
    }
  };

  let carriedMs = 0;
  let lastTick = Date.now();
  const elapsed = () => Math.max(0, Math.floor(state.elapsed_seconds + carriedMs / 1000));
  const formatClock = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;

  const updateProgress = () => {
    const answers = collect();
    const count = Object.keys(answers).length, total = numbers.length;
    answeredCount.textContent = count;
    totalCount.textContent = total;
    mobileAnsweredCount.textContent = count;
    progressCopy.textContent = `已完成 ${count} / ${total} 题`;
    progressBar.style.width = `${total ? (count / total) * 100 : 0}%`;
    for (const block of form.querySelectorAll('.question')) {
      block.classList.toggle('answered', Boolean(answers[block.dataset.number]));
      block.classList.remove('missed');
    }
  };

  const persistBrowser = () => {
    state.answers = collect();
    state.elapsed_seconds = elapsed();
    carriedMs = 0;
    lastTick = Date.now();
    writeStore({ attempt_id: state.attempt_id, answers: state.answers, elapsed_seconds: state.elapsed_seconds });
    if (attemptInput) attemptInput.value = state.attempt_id;
    if (elapsedInput) elapsedInput.value = String(state.elapsed_seconds);
  };

  const body = () => ({ attempt_id: state.attempt_id, answers: collect(), elapsed_seconds: elapsed() });
  let saveTimer;
  let saveSequence = 0;
  const save = async () => {
    const sequence = ++saveSequence;
    persistBrowser();
    setStatus('saving', '正在保存');
    try {
      const data = await window.AppRequest.requestJson(`/practice/${unitId}/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body()),
        timeout: 12000,
      });
      if (data && data.attempt_id) state.attempt_id = data.attempt_id;
      persistBrowser();
      if (sequence === saveSequence) setStatus('', '已保存到服务器和本机');
      return true;
    } catch (error) {
      if (sequence === saveSequence) {
        setStatus(
          'error',
          error.status === 401
            ? '服务器要求重新登录，草稿暂存在浏览器'
            : `草稿暂存在浏览器（${error.message}）`,
        );
      }
      return false;
    }
  };

  const scheduleSave = () => {
    persistBrowser();
    updateProgress();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 650);
  };

  const showPane = (pane) => {
    if (!practiceShell) return;
    practiceShell.dataset.mobilePane = pane;
    for (const tab of paneTabs) tab.setAttribute('aria-selected', String(tab.dataset.showPane === pane));
  };

  const focusQuestion = (number) => {
    const block = document.getElementById(`question-${number}`);
    if (!block) return;
    showPane('questions');
    block.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const target = block.querySelector('.option-chip input, input, select');
    if (target) target.focus({ preventScroll: true });
  };

  const checkWordLimit = (field) => {
    const limit = Number.parseInt(field.dataset.wordLimit || '', 10);
    if (!limit) return;
    const words = field.value.trim().split(/\s+/).filter(Boolean).length;
    field.classList.toggle('over-limit', words > limit);
    field.title = words > limit ? `本题最多 ${limit} 个词，现在写了 ${words} 个` : '';
  };

  try {
    hydrate();
    updateProgress();
    setStatus('', form.dataset.attemptId ? '已恢复上次草稿' : '本机草稿就绪');
    timer.textContent = formatClock(elapsed());
    for (const field of fields) {
      if (field.dataset && field.dataset.wordLimit) checkWordLimit(field);
    }
  } catch (error) {
    // Even if the UI wiring fails, the plain form POST still scores and stores.
    setStatus('error', '页面脚本未能初始化，仍可用「提交答案」');
  }

  if (form.dataset.attemptId && elapsedInput) elapsedInput.value = String(elapsed());

  for (const tab of paneTabs) {
    tab.addEventListener('click', () => {
      showPane(tab.dataset.showPane);
      if (practiceShell) practiceShell.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  setInterval(() => {
    const now = Date.now();
    if (!document.hidden) carriedMs += now - lastTick;
    lastTick = now;
    timer.textContent = formatClock(elapsed());
    if (!document.hidden && elapsed() % 15 === 0) persistBrowser();
  }, 1000);

  form.addEventListener('input', (event) => {
    const field = event.target;
    if (field.dataset && field.dataset.wordLimit) checkWordLimit(field);
    scheduleSave();
  });
  form.addEventListener('change', scheduleSave);
  window.addEventListener('beforeunload', persistBrowser);

  form.addEventListener('keydown', (event) => {
    const field = event.target;
    if (event.key !== 'Enter' || field.tagName !== 'INPUT') return;
    if (['hidden', 'radio', 'checkbox', 'submit'].includes(field.type)) return;
    event.preventDefault();
    const index = numbers.indexOf(field.name.slice(1));
    const next = numbers[index + 1];
    if (next) focusQuestion(next);
    else if (typeof form.requestSubmit === 'function') form.requestSubmit();
  });

  const jumpButton = el('jump-unanswered');
  if (jumpButton) {
    jumpButton.addEventListener('click', () => {
      const answers = collect();
      const missing = numbers.filter((number) => !answers[number]);
      if (!missing.length) { setStatus('', '所有题目都已作答'); return; }
      focusQuestion(missing[0]);
    });
  }

  const saveButton = el('save-now');
  if (saveButton) {
    saveButton.addEventListener('click', async () => {
      saveButton.disabled = true;
      await save();
      saveButton.disabled = false;
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const answers = collect();
    const missing = numbers.filter((number) => !answers[number]);
    if (missing.length) {
      const confirmed = typeof window.confirm !== 'function'
        || window.confirm(`还有 ${missing.length} 题未作答，未作答按错处理。仍然提交吗？`);
      if (!confirmed) {
        for (const number of missing) {
          const block = document.getElementById(`question-${number}`);
          if (block) block.classList.add('missed');
        }
        focusQuestion(missing[0]);
        return;
      }
    }
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = '正在判分…'; }
    try {
      const data = await window.AppRequest.requestJson(`/practice/${unitId}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ attempt_id: state.attempt_id, answers, elapsed_seconds: elapsed() }),
        timeout: 20000,
      });
      dropStore();
      window.location.href = data.redirect_url || `/practice/${unitId}/result/${data.attempt_id}`;
    } catch (error) {
      // Never lose a submission: the plain form POST is scored and stored by the server.
      if (submitHint) {
        submitHint.classList.add('error');
        submitHint.textContent = `脚本提交失败（${error.message}），正在改用离线方式提交…`;
      }
      persistBrowser();
      form.submit();
    }
  });
})();
