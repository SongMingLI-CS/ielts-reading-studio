// Session-scoped CSRF token plumbing shared by every page.
//
// * The token lives in a meta tag rendered by the server (bound to the session cookie).
// * Plain forms get it through `{{ csrf_field() }}`; this file is the safety net for
//   forms created later by JavaScript.
// * Multipart uploads are submitted with fetch instead of a native submit: the server
//   refuses to buffer a multipart body just to read a hidden field (that would defeat the
//   streaming upload limit), so those requests must carry the token as a header.
(() => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const token = meta ? meta.getAttribute('content') || '' : '';
  window.IELTS_CSRF = { token };

  const injectField = (form) => {
    if (!token || form.querySelector('input[name="csrf_token"]')) return;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf_token';
    input.value = token;
    form.appendChild(input);
  };

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form').forEach(injectField);
  });

  document.addEventListener(
    'submit',
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      const multipart = (form.getAttribute('enctype') || '').toLowerCase() === 'multipart/form-data';
      if (!multipart) {
        injectField(form);
        return;
      }
      event.preventDefault();
      const action = form.getAttribute('action') || window.location.pathname;
      const method = (form.getAttribute('method') || 'post').toUpperCase();
      window
        .fetch(action, {
          method,
          body: new FormData(form),
          headers: { 'X-CSRF-Token': token },
          credentials: 'same-origin',
          redirect: 'follow',
        })
        .then((response) => {
          if (response.redirected) {
            window.location.assign(response.url);
            return null;
          }
          return response.text();
        })
        .then((html) => {
          if (html === null) return;
          document.open();
          document.write(html);
          document.close();
        })
        .catch(() => {
          window.alert('上传失败：无法连接服务器，请重试。');
        });
    },
    true,
  );
})();
