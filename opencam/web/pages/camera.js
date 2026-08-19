// 摄像头详情：MJPEG 直播；文件源可回放，RTSP 不可
import { api, toast } from '../app.js';

export async function render(el, ctx = {}) {
  const id = ctx.id;
  if (!id) {
    el.innerHTML = '<p class="dim">缺少摄像头 id</p>';
    return null;
  }
  const cam = await api(`/cameras/${id}`);
  const running = cam.status === 'running';
  const isFile = cam.source_type === 'file';
  const live = running
    ? `<img class="cam-live" alt="直播" src="/cameras/${id}/live.mjpg">`
    : `<p class="dim">摄像头未运行。<img class="cam-shot" alt="暂无画面" src="/cameras/${id}/snapshot.jpg" onerror="this.style.display='none'"></p>`;
  const replay = isFile
    ? `<video class="cam-replay" controls playsinline src="/cameras/${id}/source"></video>`
    : '<p class="dim">该源为直播流，不支持回放。</p>';

  el.innerHTML = `
    <p class="meta"><a href="#/cameras">← 摄像头列表</a></p>
    <h1>${cam.name} <span class="badge ${cam.status}">${cam.status}</span></h1>
    <div class="meta mono">${cam.source_type} · ${cam.source_uri}</div>
    <div class="mt">
      ${running
        ? `<button data-act="stop">停止</button>`
        : `<button data-act="start">启动</button>`}
    </div>
    <h2 class="mt">直播</h2>
    ${live}
    <h2 class="mt">回放</h2>
    ${replay}
  `;

  const video = el.querySelector('video.cam-replay');
  if (video) {
    video.addEventListener('error', () => {
      const hint = document.createElement('p');
      hint.className = 'dim';
      hint.textContent = '浏览器无法播放该格式。';
      video.replaceWith(hint);
    });
  }

  el.querySelector('[data-act]').onclick = async (ev) => {
    const act = ev.target.dataset.act;
    try {
      await api(`/cameras/${id}/${act}`, { method: 'POST' });
      toast(act === 'start' ? '已启动' : '已停止');
      await render(el, ctx);
    } catch (err) { toast(err.message, true); }
  };

  return () => {
    const img = el.querySelector('img.cam-live');
    if (img) img.src = '';
  };
}
