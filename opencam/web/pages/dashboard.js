// 仪表盘：摄像头卡片网格，运行中的卡片约 1fps 轮询快照
import { api, fmtTime } from '../app.js';

export async function render(el) {
  el.innerHTML = '<h1>仪表盘</h1><div class="card-grid" id="grid"></div>';
  const grid = el.querySelector('#grid');
  const timers = [];

  const cameras = await api('/cameras');
  if (cameras.length === 0) {
    grid.innerHTML = '<p class="dim">还没有摄像头，去「摄像头」页添加一路。</p>';
    return null;
  }

  for (const cam of cameras) {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <h3>${cam.name} <span class="badge ${cam.status}">${cam.status}</span></h3>
      <div class="meta mono">${cam.source_type} · ${cam.source_uri}</div>
      <img class="cam-shot" alt="暂无画面">
      <div class="meta">最近事件：<span class="ev-count">—</span> · 最新：<span class="ev-last">—</span></div>
    `;
    grid.appendChild(card);

    const img = card.querySelector('.cam-shot');
    const refreshShot = () => {
      if (cam.status === 'running') {
        img.src = `/cameras/${cam.id}/snapshot.jpg?t=${Date.now()}`;
      }
    };
    refreshShot();
    timers.push(setInterval(refreshShot, 1000)); // 约 1fps 准实时

    const refreshEvents = async () => {
      try {
        const events = await api(`/events?camera_id=${cam.id}&limit=50`);
        card.querySelector('.ev-count').textContent = events.length;
        card.querySelector('.ev-last').textContent =
          events.length ? fmtTime(events[0].ts) : '无';
      } catch { /* 忽略单次失败 */ }
    };
    refreshEvents();
    timers.push(setInterval(refreshEvents, 5000));
  }

  return () => timers.forEach(clearInterval);
}
