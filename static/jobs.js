(() => {
  const terminal = new Set(['completed', 'completed_with_errors', 'blocked', 'cancelled', 'failed']);
  if (!window.JOB_ID || terminal.has(window.JOB_STATUS)) return;
  const connection = document.querySelector('#job-connection');
  let failures = 0;
  const schedule = (callback) => {
    const delays = [2000, 4000, 8000, 15000];
    window.setTimeout(callback, delays[Math.min(failures, delays.length - 1)]);
  };
  const poll = async () => {
    try {
      const data = await window.AppRequest.requestJson(`/jobs/${encodeURIComponent(window.JOB_ID)}/units`, { timeout: 10000 });
      failures = 0;
      document.querySelector('#job-status').textContent = data.job_status;
      for (const unit of data.units) {
        const row = document.querySelector(`[data-unit="${CSS.escape(unit.id)}"]`);
        if (row) row.querySelector('.status').textContent = unit.status;
      }
      if (terminal.has(data.job_status)) {
        connection.textContent = '更新完成';
        return;
      }
      connection.textContent = '已连接，正在自动更新';
    } catch (error) {
      failures += 1;
      connection.textContent = `${error.message} 将自动重试。`;
    }
    schedule(poll);
  };
  schedule(poll);
})();
