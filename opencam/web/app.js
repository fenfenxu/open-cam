// open-cam 控制台入口：hash 路由 + 公共工具
const routes = {
  dashboard: () => import('./pages/dashboard.js'),
  cameras: () => import('./pages/cameras.js'),
  rules: () => import('./pages/rules.js'),
  events: () => import('./pages/events.js'),
  training: () => import('./pages/training.js'),
  marketplace: () => import('./pages/marketplace.js'),
  settings: () => import('./pages/settings.js'),
};

// 页面卸载时的清理回调（定时器等）
let cleanup = null;

export async function api(path, options = {}) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body.detail) msg = body.detail;
    } catch { /* 非 JSON 响应 */ }
    throw new Error(msg);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

export function toast(message, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

export function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
}

export const RULE_TYPE_NAMES = {
  zone_intrusion: '区域入侵',
  loitering: '徘徊逗留',
  object_count: '人数统计',
  zone_count: '区域人数',
  line_crossing: '越线计数',
  state_classify: '状态分类告警',
};

async function render() {
  const hash = location.hash.replace(/^#\//, '') || 'dashboard';
  const name = routes[hash] ? hash : 'dashboard';

  document.querySelectorAll('#sidebar nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === name);
  });

  if (cleanup) { cleanup(); cleanup = null; }
  const app = document.getElementById('app');
  app.innerHTML = '';
  try {
    const mod = await routes[name]();
    cleanup = await mod.render(app) || null;
  } catch (err) {
    app.innerHTML = `<h1>页面加载失败</h1><p class="dim">${err.message}</p>`;
  }
}

window.addEventListener('hashchange', render);
render();
