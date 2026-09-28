/* Progressive enhancement: content stays visible without JS or motion APIs. */
(() => {
  'use strict';
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const header = document.querySelector('.site-header');
  const menuToggle = document.querySelector('.nav-toggle');
  if (header && menuToggle) {
    const compactNavigation = window.matchMedia('(max-width: 1280px)');
    const setMenu = open => {
      header.classList.toggle('menu-open', open);
      menuToggle.setAttribute('aria-expanded', String(open));
    };
    header.classList.add('nav-enhanced');
    menuToggle.addEventListener('click', () => setMenu(!header.classList.contains('menu-open')));
    document.addEventListener('click', event => {
      if (!header.contains(event.target)) setMenu(false);
    });
    header.addEventListener('keydown', event => {
      if (event.key === 'Escape' && header.classList.contains('menu-open')) {
        setMenu(false);
        menuToggle.focus();
      }
    });
    header.addEventListener('focusout', event => {
      if (!header.contains(event.relatedTarget)) setMenu(false);
    });
    compactNavigation.addEventListener('change', () => setMenu(false));
  }
  const progress = document.querySelector('.page-progress');
  let scheduled = false;
  const updateScroll = () => {
    scheduled = false;
    const distance = document.documentElement.scrollHeight - window.innerHeight;
    if (progress) progress.style.setProperty('--scroll-progress', String(distance > 0 ? Math.min(1, Math.max(0, window.scrollY / distance)) : 0));
    if (header) header.classList.toggle('is-scrolled', window.scrollY > 24);
  };
  const scheduleScroll = () => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(updateScroll); }
  };
  window.addEventListener('scroll', scheduleScroll, { passive: true });
  window.addEventListener('resize', scheduleScroll, { passive: true });
  updateScroll();

  // Animate once upon entry, with no persistent invisible state or JS-only layout.
  let observer = null;
  const activeAnimations = new Set();
  if ('IntersectionObserver' in window && Element.prototype.animate && !motionPreference.matches) {
    observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        if (motionPreference.matches || entry.target.contains(document.activeElement)) return;
        const animation = entry.target.animate([
          { opacity: 0.65, transform: 'translateY(18px)' },
          { opacity: 1, transform: 'translateY(0)' }
        ], { duration: 480, easing: 'cubic-bezier(.2,.7,.3,1)' });
        activeAnimations.add(animation);
        const clean = () => activeAnimations.delete(animation);
        animation.addEventListener('finish', clean, { once: true });
        animation.addEventListener('cancel', clean, { once: true });
      });
    }, { threshold: 0.08 });
    document.querySelectorAll('main > header, .studio-hero, .resume-card, .review-panel, .product-card, .practice-book-card, .corpus-card, .workspace-card, .vocab-card, .learning-horizon, .privacy-strip').forEach(element => observer.observe(element));
  }
  const cancelMotion = () => {
    if (!motionPreference.matches) return;
    if (observer) observer.disconnect();
    activeAnimations.forEach(animation => animation.cancel());
  };
  motionPreference.addEventListener('change', cancelMotion);
  document.addEventListener('focusin', event => {
    activeAnimations.forEach(animation => {
      const target = animation.effect && animation.effect.target;
      if (target && target.contains(event.target)) animation.cancel();
    });
  });

  // Horizontal cards work with touch, trackpad and keyboard even without buttons.
  const rail = document.getElementById('learning-paths');
  if (rail) {
    const controls = document.querySelector('.carousel-controls');
    if (controls) controls.classList.add('is-enhanced');
    const previous = document.querySelector('[data-carousel="previous"]');
    const next = document.querySelector('[data-carousel="next"]');
    const syncControls = () => {
      if (previous) previous.disabled = rail.scrollLeft <= 4;
      if (next) next.disabled = rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 4;
    };
    const move = direction => {
      rail.scrollBy({ left: direction * rail.clientWidth * 0.8, behavior: motionPreference.matches ? 'instant' : 'smooth' });
    };
    if (previous) previous.addEventListener('click', () => move(-1));
    if (next) next.addEventListener('click', () => move(1));
    rail.addEventListener('scroll', syncControls, { passive: true });
    rail.addEventListener('keydown', event => {
      if (event.target !== rail || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      move(event.key === 'ArrowRight' ? 1 : -1);
    });
    window.addEventListener('resize', syncControls, { passive: true });
    if ('ResizeObserver' in window) new ResizeObserver(syncControls).observe(rail);
    syncControls();
  }

  // Restrained hover light on pointer devices; one frame per pointer update.
  if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    document.querySelectorAll('.resume-card, .review-panel, .learning-horizon, .workspace-card').forEach(element => {
      element.setAttribute('data-spotlight', '');
      let queued = false;
      let pointer = null;
      element.addEventListener('pointermove', event => {
        if (motionPreference.matches) return;
        pointer = { x: event.clientX, y: event.clientY };
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          const rect = element.getBoundingClientRect();
          element.style.setProperty('--pointer-x', `${pointer.x - rect.left}px`);
          element.style.setProperty('--pointer-y', `${pointer.y - rect.top}px`);
        });
      }, { passive: true });
    });
  }
})();
