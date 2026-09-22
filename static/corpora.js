// 章节预览页的两处交互：每页条数自动提交，以及合并/拆分章节前的二次确认。
//
// 原先分别是内联 `onchange="this.form.submit()"` 与 `onsubmit="return confirm(...)"`。
// 两者都会被 CSP 的 script-src 拦掉（script-src 只有 'self' 与 nonce，没有
// 'unsafe-inline'）：前者选完没反应，后者更糟——确认框根本不出现，表单直接提交，
// 破坏性的边界修改失去了确认步骤。改成外部脚本后行为与原来一致。
(() => {
  document.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const select = target.closest('[data-auto-submit]');
    if (!select || !select.form) return;
    if (typeof select.form.requestSubmit === 'function') select.form.requestSubmit();
    else select.form.submit();
  });

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const message = form.dataset.confirm;
    if (message && !window.confirm(message)) event.preventDefault();
  });
})();
