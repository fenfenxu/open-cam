// 仪表盘：摄像头卡片网格，运行中的卡片约 1fps 轮询快照；卡片下方为 24h 客流图
import { api, fmtTime } from '../app.js';

// 24 小时进/出双列柱状图（纯 canvas，无第三方库）
function drawFootfall(canvas, buckets) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const max = Math.max(1, ...buckets.map((b) => Math.max(b.in, b.out)));
  const groupW = W / 24;
  const barW = Math.max(1, (groupW - 3) / 2);
  buckets.forEach((b, i) => {
    const x0 = i * groupW + 1;
    const inH = (b.in / max) * (H - 12);
    const outH = (b.out / max) * (H - 12);
    ctx.fillStyle = '#3b9eff'; // 进
    ctx.fillRect(x0, H - inH, barW, inH);
    ctx.fillStyle = '#e5b545'; // 出
    ctx.fillRect(x0 + barW + 1, H - outH, barW, outH);
  });
  // 0/12/23 时刻刻度
  ctx.fillStyle = '#8a94a3';
  ctx.font = '9px sans-serif';
  ctx.fillText('0', 1, H - 1);
  ctx.fillText('12', 12 * groupW, H - 1);
  ctx.fillText('23', 23 * groupW, H - 1);
}

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
      <div class="meta mt">
        今日客流 <span class="foot-total"></span>
        <span style="color:#3b9eff">■ 进</span> <span style="color:#e5b545">■ 出</span>
      </div>
      <canvas class="footfall-chart" width="260" height="64"></canvas>
      <div class="meta foot-empty" hidden>暂无客流数据（先配置「越线计数」规则）</div>
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

    const chart = card.querySelector('.footfall-chart');
    const refreshFootfall = async () => {
      try {
        const data = await api(`/api/stats/footfall?camera_id=${cam.id}`);
        const total = data.total_in + data.total_out;
        card.querySelector('.foot-total').textContent =
          total ? `进 ${data.total_in} / 出 ${data.total_out}` : '';
        card.querySelector('.foot-empty').hidden = total > 0;
        chart.hidden = total === 0;
        if (total) drawFootfall(chart, data.buckets);
      } catch { /* 忽略单次失败 */ }
    };
    refreshFootfall();
    timers.push(setInterval(refreshFootfall, 30000));
  }

  return () => timers.forEach(clearInterval);
}
