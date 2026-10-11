(() => {
  const form = document.getElementById('practice-form');
  if (!form) return;
  const el = id => document.getElementById(id), unit = form.dataset.unitId;
  const mode = document.body.dataset.mode, shell = document.querySelector('.practice-shell');
  const passage = document.querySelector('.passage-scroll'), questions = document.querySelector('.questions-scroll');
  const fields = [...form.querySelectorAll('[name^="q"]')];
  const blocks = [...form.querySelectorAll('.question')], numbers = blocks.map(b => b.dataset.number);
  const parse = text => { try { return JSON.parse(text); } catch (_) { return null; } };
  let state = parse(el('session-state').textContent);
  const newId = () => {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (window.crypto?.getRandomValues) window.crypto.getRandomValues(bytes);
    else bytes.forEach((_, i) => { bytes[i] = Math.floor(Math.random() * 256); });
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  };
  let id = state?.attempt_id || form.elements.attempt_id.value || newId(), revision = state?.revision || 0;
  let ui = { current: Number(numbers[0]), flags: [], highlights: {}, text_size: 18, pane: 'reading', ...(state?.ui || {}) };
  let dirty = false, generation = 0, blocked = false, submitting = false, starting = false;
  let queue = Promise.resolve(), pending = 0, timer, scrollLock = false, restoring = true;
  let carried = state?.elapsed_seconds || 0, clockBase = performance.now();
  let serverBase = state?.server_time || 0, serverAt = performance.now();
  const serverNow = () => serverBase + (performance.now() - serverAt) / 1000;
  const elapsed = () => Math.floor(carried + (document.hidden ? 0 : (performance.now() - clockBase) / 1000));
  const status = (text, error = false) => { el('autosave-status').textContent = text; el('autosave-status').classList.toggle('error', error); };
  const request = (path, data) => window.AppRequest.requestJson(`/practice/${unit}/${path}`, data === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const collect = () => {
    const answers = {};
    for (const n of numbers) {
      const group = fields.filter(f => f.name === `q${n}`);
      const selected = group.find(f => f.type === 'radio' && f.checked);
      if (group.some(f => f.type === 'checkbox')) { const values = group.filter(f => f.checked).map(f => f.value); if (values.length) answers[n] = values; continue; }
      const text = group.find(f => !['radio','checkbox'].includes(f.type));
      const value = selected?.value ?? text?.value?.trim() ?? '';
      if (value) answers[n] = value;
    }
    return answers;
  };
  const hydrate = answers => {
    fields.forEach(f => { const value = answers?.[f.name.slice(1)] || ''; if (f.type === 'radio' || f.type === 'checkbox') f.checked = Array.isArray(value) ? value.includes(f.value) : f.value === value; else f.value = value; });
  };
  const capture = (scroller, selector) => {
    const children = [...scroller.querySelectorAll(selector)], top = scroller.getBoundingClientRect().top;
    if (scroller.scrollTop < 2) return { anchor: null, offset: 0 };
    const node = children.find(n => n.getBoundingClientRect().bottom > top + 2);
    return node ? { anchor: node.id, offset: node.getBoundingClientRect().top - top } : { anchor: null, offset: 0 };
  };
  const captureUI = () => {
    if (restoring) return;
    if (getComputedStyle(passage.parentElement).display !== 'none') {
      const a = capture(passage, '.paragraph'); ui.reading_anchor = a.anchor; ui.reading_offset = a.offset;
    }
    if (getComputedStyle(questions.parentElement).display !== 'none') {
      const a = capture(questions, '.question'); ui.question_anchor = a.anchor; ui.question_offset = a.offset;
    }
  };
  const restore = () => {
    restoring = true;
    requestAnimationFrame(() => {
      [[passage, ui.reading_anchor, ui.reading_offset], [questions, ui.question_anchor, ui.question_offset]].forEach(([s, a, offset]) => {
        if (getComputedStyle(s.parentElement).display === 'none') return;
        const node = a && el(a);
        if (node && s.contains(node)) s.scrollTop += node.getBoundingClientRect().top - s.getBoundingClientRect().top - (offset || 0);
        else s.scrollTop = 0;
      });
      requestAnimationFrame(() => { restoring = false; });
    });
  };
  const progress = () => {
    const answers = collect(), count = Object.keys(answers).length;
    el('answered-count').textContent = count; el('total-count').textContent = numbers.length;
    el('mobile-answered-count').textContent = count; el('progress-copy').textContent = `${count} / ${numbers.length} 已作答`;
    blocks.forEach(b => {
      const n = Number(b.dataset.number), answered = Boolean(answers[n]);
      b.classList.toggle('current', n === ui.current); b.classList.toggle('answered', answered);
      b.querySelector('[data-flag]').setAttribute('aria-pressed', String(ui.flags.includes(n)));
      const button = el(`nav-${n}`);
      button.classList.toggle('answered', answered); button.classList.toggle('current', n === ui.current); button.classList.toggle('review', ui.flags.includes(n));
      button.setAttribute('aria-label', `第 ${n} 题，${answered ? '已作答' : '未作答'}${ui.flags.includes(n) ? '，待复查' : ''}`);
      if (n === ui.current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
    });
    const group = el(`question-${ui.current}`)?.closest('.question-group');
    el('question-context').textContent = group?.dataset.label || '';
    document.querySelectorAll('[data-jump]').forEach(b => b.classList.toggle('active', el(`question-${b.dataset.jump}`)?.closest('.question-group') === group));
  };
  numbers.forEach(n => { const button = document.createElement('button'); button.type = 'button'; button.id = `nav-${n}`; button.textContent = n; button.addEventListener('click', () => jump(n)); el('numbers').append(button); });
  const follow = data => { if (data.status === 'submitted' && data.redirect_url) { dirty = false; submitting = true; location.assign(data.redirect_url); return true; } return false; };
  const accept = data => {
    if (follow(data)) return;
    state = { ...state, ...data }; revision = data.revision; id = data.attempt_id;
    serverBase = data.server_time; serverAt = performance.now();
    updateClock();
    form.elements.attempt_id.value = id; el('revision').value = revision;
    const address = new URL(location.href); address.searchParams.set('attempt', id); address.searchParams.delete('fresh'); history.replaceState(null, '', address);
  };
  const fail = error => {
    if (error.code === 'submitted') {
      request(`state/${id}`).then(follow).catch(() => { status('本次练习已提交，请重新加载查看成绩。', true); el('reload-state').hidden = false; });
    }
    if (error.status === 409) {
      blocked = true; status(error.message, true); el('reload-state').hidden = false;
      el('submit').disabled = true;
    } else status(`${error.message} 未保存的答案仍在本页，请重试。`, true);
  };
  const start = async minutes => {
    if (starting) return false;
    starting = true;
    try {
      accept(await request('start', { attempt_id: id, mode, minutes }));
      fields.forEach(f => f.disabled = false); status('服务器草稿就绪'); return true;
    } catch (error) { fail(error); return false; }
    finally { starting = false; if (state && dirty) setTimeout(save, 0); }
  };
  const save = () => {
    clearTimeout(timer);
    if (blocked || submitting) return Promise.resolve(false);
    if (!state) return mode === 'study' ? start(20).then(ok => ok ? save() : false) : Promise.resolve(false);
    pending++;
    queue = queue.then(async () => {
      if (blocked || submitting) return false;
      captureUI(); const version = generation;
      status('正在保存…');
      try {
        const data = await request('save', { attempt_id: id, revision, answers: collect(), elapsed_seconds: elapsed(), ui });
        accept(data); if (version === generation) dirty = false;
        status(data.status === 'submitted' ? '已交卷' : '已保存到服务器'); return true;
      } catch (error) { fail(error); return false; }
    }).finally(() => { pending--; });
    return queue;
  };
  const changed = () => { generation++; dirty = true; progress(); clearTimeout(timer); timer = setTimeout(save, 500); };
  const showPane = (pane, saveChange = true) => {
    captureUI(); ui.pane = pane; shell.dataset.mobilePane = pane;
    document.querySelectorAll('[data-show-pane]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.showPane === pane)));
    restore(); if (saveChange) changed();
  };
  const jump = (n, groupNav = false) => {
    const block = el(`question-${n}`); if (!block) return;
    showPane('questions', false); ui.current = Number(n); scrollLock = true;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const target = groupNav ? block.closest(".question-group") : block;
      // The scroll viewport starts below the fixed panel/type navigation.
      const offset = parseFloat(getComputedStyle(questions).paddingTop) || 12;
      questions.scrollTop += target.getBoundingClientRect().top - questions.getBoundingClientRect().top - offset;
      if (groupNav) {
        // Focus the type heading, not an answer below a long instruction/list.
        const heading = target.querySelector('h3');
        heading.tabIndex = -1; heading.focus({ preventScroll: true });
      } else block.querySelector('input,select')?.focus({ preventScroll: true });
      setTimeout(() => { scrollLock = false; }, 180); changed();
    }));
    progress();
  };
  fields.forEach(f => {
    f.addEventListener('focus', () => { ui.current = Number(f.name.slice(1)); changed(); });
    f.addEventListener('input', () => {
      if (f.dataset.wordLimit) {
        const count = (f.value.match(/[\p{L}\p{N}_]+(?:['’\-][\p{L}\p{N}_]+)*/gu) || []).length, limit = Number(f.dataset.wordLimit);
        f.classList.toggle('over-limit', count > limit);
        f.parentElement.querySelector('.limit').textContent = count > limit ? `最多 ${limit} 个词；当前 ${count} 个词` : '';
      }
      changed();
    });
    f.addEventListener('change', () => { const box=f.closest('[data-max-choices]'); if(f.type==='checkbox' && f.checked && box && box.querySelectorAll('input:checked').length>Number(box.dataset.maxChoices)){f.checked=false;status(`此题请选择 ${box.dataset.maxChoices} 项`); } changed(); });
  });
  document.querySelectorAll('[data-flag]').forEach(b => b.addEventListener('click', () => { const n = Number(b.dataset.flag); ui.flags = ui.flags.includes(n) ? ui.flags.filter(v => v !== n) : [...ui.flags, n]; changed(); }));
  document.querySelectorAll('[data-jump]').forEach(b => b.addEventListener('click', () => jump(b.dataset.jump, true)));
  document.querySelectorAll('[data-show-pane]').forEach(b => b.addEventListener('click', () => showPane(b.dataset.showPane)));
  el('save-now').addEventListener('click', save);
  el('reload-state').addEventListener('click', () => { if (!dirty || confirm('重新加载会舍弃本页尚未保存的修改。确认加载服务器版本？')) { dirty = false; location.assign(`/practice/${unit}?attempt=${id}`); } });
  el('jump-unanswered').addEventListener('click', () => { const a = collect(); const n = numbers.find(n => !a[n]); if (n) jump(n); else status('全部题目已作答'); });
  const resizeText = delta => { captureUI(); ui.text_size = Math.max(18, Math.min(22, ui.text_size + delta)); document.body.style.setProperty('--text-size', `${ui.text_size}px`); restore(); changed(); };
  el('text-smaller').addEventListener('click', () => resizeText(-1)); el('text-larger').addEventListener('click', () => resizeText(1));
  const original = new Map([...document.querySelectorAll('.paragraph p')].map(p => [p.parentElement.id, p.textContent]));
  const paint = () => {
    original.forEach((text, key) => {
      const p = el(key).querySelector('p'); p.replaceChildren(); let pos = 0;
      const intervals = (ui.highlights[key] || []).filter(a => Array.isArray(a) && a.length === 2 && a[0] >= 0 && a[1] <= text.length && a[1] > a[0]).sort((a,b) => a[0]-b[0]);
      intervals.forEach(([a,b]) => { a = Math.max(a,pos); if (b <= a) return; p.append(document.createTextNode(text.slice(pos,a))); const mark = document.createElement('mark'); mark.textContent = text.slice(a,b); p.append(mark); pos=b; });
      p.append(document.createTextNode(text.slice(pos)));
    });
  };
  el('highlight')?.addEventListener('click', () => {
    const selection = getSelection(); let p = selection?.anchorNode?.parentElement?.closest('.paragraph p');
    if (p && p.contains(selection.focusNode) && !selection.isCollapsed) {
      const range = selection.getRangeAt(0), pre = document.createRange(); pre.selectNodeContents(p); pre.setEnd(range.startContainer, range.startOffset);
      const start = pre.toString().length, end = start + range.toString().length, key = p.parentElement.id;
      ui.highlights[key] = [...(ui.highlights[key] || []), [start,end]]; selection.removeAllRanges();
    } else {
      p = [...passage.querySelectorAll('.paragraph')].find(n => n.getBoundingClientRect().bottom > passage.getBoundingClientRect().top + 10)?.querySelector('p');
      if (!p) return; ui.highlights[p.parentElement.id] = [[0,p.textContent.length]];
    }
    paint(); changed();
  });
  el('clear-highlights')?.addEventListener('click', () => { ui.highlights = {}; paint(); changed(); });
  const submit = async () => {
    if (submitting || blocked || !state) return;
    el('submit-dialog').close(); clearTimeout(timer);
    // Drain queued writes; then submit exactly the acknowledged revision.
    if (!await save()) return;
    if (submitting) return;
    submitting = true; el('submit').disabled = true; status('正在判分…');
    try { const data = await request('submit', { attempt_id:id, revision, answers:collect(), elapsed_seconds:elapsed(), ui }); dirty=false; location.assign(data.redirect_url); }
    catch(error) { submitting=false; el('submit').disabled=blocked; fail(error); sync(); }
  };
  form.addEventListener('submit', event => {
    event.preventDefault(); if (!state || submitting || blocked) return;
    const a=collect(), missing=numbers.filter(n => !a[n]).length;
    el('missing-copy').textContent = `还有 ${missing} 题未作答，${ui.flags.length} 题标记复查。未作答按错处理。提交后答案将锁定。`;
    el('submit-dialog').showModal();
  });
  el('confirm-submit').addEventListener('click', submit); el('cancel-submit').addEventListener('click', () => el('submit-dialog').close());
  el('start-exam').addEventListener('click', async () => { if (await start(Number(el('exam-duration').value))) el('start-dialog').close(); });
  el('start-dialog').addEventListener('cancel', event => event.preventDefault());
  const format = s => `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  let polling = false;
  const sync = async () => {
    if (!state || polling || pending || submitting || blocked) return;
    polling=true;
    try {
      const data = await request(`state/${id}`);
      if (follow(data)) return;
      if (!pending && data.revision > revision) { blocked=true; el('submit').disabled=true; el('reload-state').hidden=false; status('另一页面更新了草稿，请重新加载服务器版本。', true); return; }
      serverBase=data.server_time; serverAt=performance.now();
    } catch(error) { if (mode==='exam') status('连接中断，计时继续；恢复连接后将同步交卷状态。',true); }
    finally { polling=false; }
  };
  function updateClock() {
    const seconds = mode==='exam' ? Math.max(0,Math.ceil((state?.deadline || serverNow()) - serverNow())) : elapsed();
    el('timer').textContent=mode==='exam' && !state?.deadline ? '未开始' : format(seconds); el('timer').classList.toggle('urgent',mode==='exam' && seconds<120);
    el('elapsed-seconds').value=elapsed();
    if(mode==='exam' && state?.deadline && seconds===0) { fields.forEach(f=>f.disabled=true); el('submit').disabled=true; sync(); }
  }
  setInterval(updateClock, 1000);
  setInterval(() => { if (dirty && !blocked) save(); else sync(); }, 10000);
  document.addEventListener('visibilitychange', () => {
    const now = performance.now();
    if (document.hidden) { carried += (now - clockBase) / 1000; clockBase = now; if (dirty) save(); }
    else { clockBase = now; sync(); }
  });
  window.addEventListener('beforeunload', event => { if (dirty && !submitting) { event.preventDefault(); event.returnValue=''; } });
  document.querySelectorAll('a').forEach(a => a.addEventListener('click', async event => { if (!dirty || event.metaKey || event.ctrlKey || a.getAttribute('href').startsWith('#')) return; event.preventDefault(); if (await save()) location.assign(a.href); }));
  let scrollSave;
  const scrolled = event => {
    if(restoring || scrollLock) return;
    captureUI();
    if(event.currentTarget === questions && getComputedStyle(questions.parentElement).display !== 'none') {
      const bounds=questions.getBoundingClientRect(), focused=document.activeElement;
      const active=fields.includes(focused) ? focused.closest('.question') : null;
      const rect=active?.getBoundingClientRect();
      // Keep the answer being edited current while its question is visible.
      const current=rect && rect.bottom>bounds.top && rect.top<bounds.bottom ? active
        : blocks.find(b=>b.getBoundingClientRect().bottom>bounds.top+10);
      if(current) { ui.current=Number(current.dataset.number); progress(); }
    }
    generation++; dirty=true; clearTimeout(scrollSave); scrollSave=setTimeout(save,800);
  };
  passage.addEventListener('scroll',scrolled); questions.addEventListener('scroll',scrolled);
  const revealFocusedAnswer = () => {
    const input = document.activeElement;
    if (!input || !fields.includes(input) || getComputedStyle(questions.parentElement).display === 'none') return;
    const bounds = questions.getBoundingClientRect(), rect = input.getBoundingClientRect();
    if (rect.bottom > bounds.bottom - 10) questions.scrollTop += rect.bottom - bounds.bottom + 10;
    else if (rect.top < bounds.top + 10) questions.scrollTop -= bounds.top + 10 - rect.top;
  };
  const afterResize = () => { restore(); requestAnimationFrame(() => requestAnimationFrame(revealFocusedAnswer)); };
  window.addEventListener('resize', afterResize);
  form.addEventListener('focusin', () => requestAnimationFrame(revealFocusedAnswer));
  const resizer=el('pane-resizer');
  const width = v => { captureUI(); v=Math.min(65,Math.max(35,v)); shell.style.gridTemplateColumns=`minmax(0,${v}fr) 8px minmax(0,${100-v}fr)`; resizer.setAttribute('aria-valuenow',v); restore(); };
  resizer.addEventListener('keydown', e=>{if(['ArrowLeft','ArrowRight','Home'].includes(e.key)){e.preventDefault();width(e.key==='Home'?53:Number(resizer.getAttribute('aria-valuenow'))+(e.key==='ArrowRight'?2:-2));}});
  resizer.addEventListener('pointerdown',e=>{if(e.button===0){resizer.setPointerCapture(e.pointerId);e.preventDefault();}});
  resizer.addEventListener('pointermove',e=>{if(resizer.hasPointerCapture(e.pointerId)){const r=shell.getBoundingClientRect();width(100*(e.clientX-r.left)/r.width);}});
  resizer.addEventListener('pointerup',e=>{if(resizer.hasPointerCapture(e.pointerId))resizer.releasePointerCapture(e.pointerId);});
  if(window.visualViewport){const base=window.innerHeight;const fit=()=>{const h=window.visualViewport.height;document.body.classList.toggle('keyboard-open',base-h>140);document.body.style.setProperty('--visible-height',`${h}px`);requestAnimationFrame(() => requestAnimationFrame(revealFocusedAnswer));};window.visualViewport.addEventListener('resize',fit);fit();}
  hydrate(state?.answers || {}); document.body.style.setProperty('--text-size',`${ui.text_size}px`); paint(); showPane(ui.pane,false); progress();
  if(state) { accept(state); status('已恢复服务器草稿'); }
  else if(mode==='exam'){fields.forEach(f=>f.disabled=true);el('start-dialog').showModal();}
  else start(20);
})();
