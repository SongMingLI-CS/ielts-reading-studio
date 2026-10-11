(() => {
  const header = document.querySelector('.site-header');
  const toggle = document.querySelector('.nav-toggle');
  const navigation = document.querySelector('#site-navigation');
  if (!header || !toggle || !navigation) return;
  const close = () => { header.classList.remove('menu-open'); toggle.setAttribute('aria-expanded', 'false'); };
  toggle.addEventListener('click', () => {
    const open = !header.classList.contains('menu-open');
    header.classList.toggle('menu-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('click', event => { if (!header.contains(event.target)) close(); });
  header.addEventListener('keydown', event => { if (event.key === 'Escape') { close(); toggle.focus(); } });
  window.matchMedia('(min-width: 901px)').addEventListener('change', close);
})();
