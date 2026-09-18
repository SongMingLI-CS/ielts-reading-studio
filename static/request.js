(() => {
  class RequestError extends Error {
    constructor(message, { status = 0, code = 'request_failed', body = null } = {}) {
      super(message);
      this.name = 'RequestError';
      this.status = status;
      this.code = code;
      this.body = body;
    }
  }

  const detailMessage = (body) => {
    const detail = body && body.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === 'string') return detail[0].msg;
    return '';
  };

  const requestJson = async (url, options = {}) => {
    const { timeout = 15000, signal, ...fetchOptions } = options;
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, { once: true });
    }
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    try {
      const response = await fetch(url, { ...fetchOptions, signal: controller.signal });
      let body = null;
      try { body = await response.json(); } catch (_) { /* converted to a useful error below */ }
      if (!response.ok) {
        const message = detailMessage(body) || `服务器返回错误（${response.status}）`;
        const code = body && body.detail && body.detail.code;
        throw new RequestError(message, { status: response.status, code: code || 'http_error', body });
      }
      if (body === null) throw new RequestError('服务器返回了无法读取的数据。', { status: response.status, code: 'invalid_json' });
      return body;
    } catch (error) {
      if (error instanceof RequestError) throw error;
      if (error && error.name === 'AbortError') {
        throw new RequestError(timedOut ? '请求超时，请检查连接后重试。' : '请求已取消。', { code: timedOut ? 'timeout' : 'aborted' });
      }
      throw new RequestError('无法连接服务器，请检查网络后重试。', { code: 'network_error' });
    } finally {
      window.clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', abort);
    }
  };

  window.AppRequest = { RequestError, requestJson };
})();