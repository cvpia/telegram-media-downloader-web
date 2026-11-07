(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let toastTimer = null;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[character]);
  }

  async function api(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    const timeoutMs = Number(options.timeoutMs || 0);
    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    let body = options.body;
    if (body !== undefined && body !== null && !(body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
      body = JSON.stringify(body);
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      headers.set('X-CSRF-Token', csrfToken);
    }
    try {
      const response = await fetch(url, {
        method,
        headers,
        body,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller?.signal
      });
      if (response.status === 401) {
        if (!['/login', '/setup'].includes(window.location.pathname)) window.location.href = '/login';
        throw new Error('登录状态已失效');
      }
      const contentType = response.headers.get('content-type') || '';
      const payload = contentType.includes('application/json') ? await response.json() : await response.text();
      if (!response.ok) {
        let message = payload?.detail || payload || `请求失败 (${response.status})`;
        if (Array.isArray(message)) {
          message = message.map((item) => item.msg || JSON.stringify(item)).join('；');
        }
        throw new Error(String(message));
      }
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error('请求超时，请稍后重试');
      throw error;
    } finally {
      if (timeoutId !== null) clearTimeout(timeoutId);
    }
  }

  function toast(message, type = 'info') {
    const element = document.getElementById('toast');
    if (!element) return;
    element.textContent = message;
    element.dataset.type = type;
    element.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => element.classList.remove('show'), 3500);
  }

  function resetVideoPlayer() {
    const dialog = document.getElementById('video-dialog');
    const player = document.getElementById('video-player');
    if (!dialog || !player) return;
    if (typeof player.pause === 'function') player.pause();
    if (typeof player.removeAttribute === 'function') player.removeAttribute('src');
    if (typeof player.load === 'function') player.load();
  }

  function openVideo(url, title = '视频播放', subtitle = '在线播放 · 支持拖动进度') {
    const dialog = document.getElementById('video-dialog');
    const player = document.getElementById('video-player');
    if (!dialog || !player || !url) return;
    document.getElementById('video-dialog-title').textContent = title;
    document.getElementById('video-dialog-subtitle').textContent = subtitle;
    player.src = url;
    if (!dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    }
    if (typeof player.play === 'function') player.play().catch(() => {});
  }

  function closeVideo() {
    const dialog = document.getElementById('video-dialog');
    if (!dialog) return;
    if (dialog.open && typeof dialog.close === 'function') dialog.close();
    else {
      dialog.removeAttribute('open');
      resetVideoPlayer();
    }
  }

  const videoDialog = document.getElementById('video-dialog');
  if (videoDialog) {
    document.getElementById('close-video-dialog')?.addEventListener('click', closeVideo);
    videoDialog.addEventListener('click', (event) => {
      if (event.target === videoDialog) closeVideo();
    });
    videoDialog.addEventListener('close', resetVideoPlayer);
  }

  const logout = document.getElementById('logout-button');
  if (logout) {
    logout.addEventListener('click', async () => {
      try {
        await api('/api/auth/logout', { method: 'POST' });
        localStorage.removeItem('tmd:dialogs:v2');
      } finally {
        window.location.href = '/login';
      }
    });
  }

  window.TMD = { api, escape: escapeHtml, toast, openVideo, closeVideo };
})();
