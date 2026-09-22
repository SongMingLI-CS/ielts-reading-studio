// 打印页（错题本 / 生词本 / 知识点手册）的「打印 / 存为 PDF」按钮。
//
// 这些按钮原先写成内联 `onclick="window.print()"`，会被安全策略静默拦掉：CSP 的
// script-src 只允许 'self' 与每响应 nonce，而内联事件处理器要求 'unsafe-inline'
// （或 hash + 'unsafe-hashes'）。表现就是按钮点了完全没反应，也没有报错提示。
// 改为外部脚本 + `data-print-trigger`：行为不变，且符合策略。
(() => {
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const trigger = target.closest('[data-print-trigger]');
    if (!trigger) return;
    event.preventDefault();
    window.print();
  });
})();
