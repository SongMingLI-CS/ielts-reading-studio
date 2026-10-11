(() => {
  // Check the existing protected route before navigation so HTTP 409 has feedback.
  const dialog = document.getElementById('analysis-dialog');
  let analysisLink, controller, navigation = 0;
  const openAnalysis = async link => {
    if (link.getAttribute('aria-busy') === 'true') return;
    controller?.abort(); controller = new AbortController();
    const request = controller, version = ++navigation;
    analysisLink = link; link.setAttribute('aria-busy', 'true');
    const title = document.getElementById('analysis-title'), message = document.getElementById('analysis-message');
    const exam = document.getElementById('analysis-exam'), retry = document.getElementById('analysis-retry');
    title.textContent = '正在打开解析'; message.textContent = '正在检查解析是否可以访问…';
    exam.hidden = true; retry.hidden = true;
    if (!dialog.open) dialog.showModal();
    const timeout = setTimeout(() => request.abort(), 15000);
    try {
      const response = await fetch(link.href, { headers: { Accept: 'application/json' }, cache: 'no-store', signal: request.signal });
      if (version !== navigation) return;
      if (response.ok) { dialog.close(); location.assign(link.href); return; }
      let body; try { body = await response.json(); } catch (_) { /* generic HTTP error below */ }
      const reason = typeof body?.detail === 'string' ? body.detail : `服务器返回错误（${response.status}）`;
      title.textContent = '暂时无法打开解析';
      message.textContent = response.status === 409
        ? `${reason}。请先返回同篇限时练习完成交卷，再打开全篇解析。`
        : `${reason}。请稍后重试。`;
      if (response.status === 409) { exam.href = link.dataset.examUrl; exam.hidden = false; }
      retry.hidden = false;
    } catch (_) {
      if (version !== navigation) return;
      title.textContent = '暂时无法打开解析'; message.textContent = '连接中断或请求超时，请检查网络后重试。'; retry.hidden = false;
    } finally { clearTimeout(timeout); link.removeAttribute('aria-busy'); }
  };
  if (dialog) {
    document.querySelectorAll('[data-analysis-link]').forEach(link => link.addEventListener('click', event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); openAnalysis(link);
    }));
    document.getElementById('analysis-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { navigation++; controller?.abort(); });
    document.getElementById('analysis-retry').addEventListener('click', () => openAnalysis(analysisLink));
  }

  const shell = document.querySelector('.result-workspace');
  if (!shell) return;
  const passage = shell.querySelector('.passage-scroll'), questions = shell.querySelector('.questions-scroll');
  const blocks = [...questions.querySelectorAll('.result-question')], buttons = [...document.querySelectorAll('[data-result]')];
  let scrolling = false, jumpVersion = 0, scrollFrame;
  const current = number => {
    blocks.forEach(q => q.classList.toggle('current', q.id === `review-${number}`));
    buttons.forEach(b => {
      const active = b.dataset.result === String(number); b.classList.toggle('current', active);
      if (active) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });
  };
  const syncCurrent = () => {
    if (scrolling || !questions.clientHeight) return;
    const bounds = questions.getBoundingClientRect();
    let main, largest = 0;
    blocks.filter(q => !q.hidden).forEach(q => {
      const rect = q.getBoundingClientRect(), visible = Math.max(0, Math.min(rect.bottom, bounds.bottom) - Math.max(rect.top, bounds.top));
      if (visible > largest) { largest = visible; main = q; }
    });
    if (main) current(main.id.slice('review-'.length));
  };
  const show = pane => {
    shell.dataset.mobilePane = pane;
    document.querySelectorAll('[data-show-pane]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.showPane === pane)));
  };
  document.querySelectorAll('[data-show-pane]').forEach(b => b.addEventListener('click', () => { show(b.dataset.showPane); requestAnimationFrame(syncCurrent); }));
  document.querySelectorAll('[data-evidence]').forEach(b => b.addEventListener('click', () => {
    show('reading'); requestAnimationFrame(() => {
      document.querySelectorAll('.evidence').forEach(p => p.classList.remove('evidence'));
      const p = document.getElementById(`paragraph-${b.dataset.evidence}`); if (!p) return;
      p.classList.add('evidence'); passage.scrollTop += p.getBoundingClientRect().top - passage.getBoundingClientRect().top - 20;
      passage.focus({ preventScroll: true });
    });
  }));
  const filter = document.getElementById('wrong-only');
  filter.addEventListener('click', () => {
    const value = filter.getAttribute('aria-pressed') !== 'true'; filter.setAttribute('aria-pressed', String(value));
    blocks.forEach(q => q.hidden = value && q.dataset.correct === 'true'); requestAnimationFrame(syncCurrent);
  });
  buttons.forEach(b => b.addEventListener('click', () => {
    const q = document.getElementById(`review-${b.dataset.result}`);
    if (q.hidden) { filter.setAttribute('aria-pressed', 'false'); blocks.forEach(q => q.hidden = false); }
    show('questions'); current(b.dataset.result); scrolling = true;
    const version = ++jumpVersion;
    requestAnimationFrame(() => {
      if (version !== jumpVersion) return;
      questions.scrollTop += q.getBoundingClientRect().top - questions.getBoundingClientRect().top - 12;
      q.querySelector('[data-evidence]')?.focus({ preventScroll: true });
      requestAnimationFrame(() => requestAnimationFrame(() => { if (version === jumpVersion) scrolling = false; }));
    });
  }));
  questions.addEventListener('scroll', () => {
    if (scrolling) return;
    cancelAnimationFrame(scrollFrame); scrollFrame = requestAnimationFrame(syncCurrent);
  });
  window.addEventListener('resize', () => requestAnimationFrame(syncCurrent));
  requestAnimationFrame(syncCurrent);
})();
