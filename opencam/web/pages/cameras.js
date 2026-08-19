// 摄像头管理：列表 + 新建 + 启停 + 行内保存 + 已上传视频
import { api, toast } from '../app.js';

function dash(value) {
  return value == null || value === '' ? '—' : value;
}

export async function render(el) {
  el.innerHTML = `
    <h1>摄像头</h1>
    <div class="card">
      <div class="form-row">
        <label>名称</label><input id="c-name" placeholder="门口">
        <label>类型</label>
        <select id="c-type">
          <option value="file">视频文件</option>
          <option value="rtsp">RTSP 流</option>
        </select>
      </div>
      <div class="form-row">
        <label>源地址</label><input id="c-uri" size="46"
          placeholder="/path/to/video.mp4 或 rtsp://...">
        <input type="file" id="c-file" accept="video/*,.mkv,.ts" hidden>
        <button id="c-browse" type="button">选择文件…</button>
        <label><input type="checkbox" id="c-autostart"> 创建即启动</label>
        <button id="c-create">添加</button>
      </div>
    </div>
    <div class="mt" id="list"></div>
    <h2 class="mt">已上传视频</h2>
    <div class="mt" id="videos"></div>
  `;

  const typeSel = el.querySelector('#c-type');
  const browseBtn = el.querySelector('#c-browse');
  const fileInput = el.querySelector('#c-file');
  const uriInput = el.querySelector('#c-uri');

  function syncBrowse() {
    // 只有视频文件类型才需要文件选择框
    browseBtn.style.display = typeSel.value === 'file' ? '' : 'none';
  }
  typeSel.onchange = syncBrowse;
  syncBrowse();

  async function reloadVideos() {
    const videos = await api('/videos');
    const box = el.querySelector('#videos');
    if (videos.length === 0) {
      box.innerHTML = '<p class="dim">暂无已上传视频。</p>';
      return;
    }
    box.innerHTML = `
      <table>
        <tr><th>ID</th><th>文件名</th><th>大小</th><th>时长</th><th>分辨率</th><th>操作</th></tr>
        ${videos.map((v) => `
          <tr>
            <td class="mono">${v.id}</td>
            <td>${v.filename}</td>
            <td class="mono">${v.size_bytes}</td>
            <td>${dash(v.duration_sec)}</td>
            <td>${v.width && v.height ? `${v.width}×${v.height}` : '—'}</td>
            <td>
              <button class="danger" data-act="vdel" data-id="${v.id}">删除</button>
            </td>
          </tr>`).join('')}
      </table>`;
  }

  browseBtn.onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    const file = fileInput.files[0];
    if (!file) return;
    // 浏览器拿不到本地完整路径，先上传到服务端再用保存后的路径
    const form = new FormData();
    form.append('file', file);
    try {
      const resp = await fetch('/cameras/upload', { method: 'POST', body: form });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
      uriInput.value = body.path;
      toast('文件已上传');
      await reloadVideos();
    } catch (err) { toast(err.message, true); }
    fileInput.value = '';
  };

  async function reloadCameras() {
    const cameras = await api('/cameras');
    const list = el.querySelector('#list');
    if (cameras.length === 0) {
      list.innerHTML = '<p class="dim">暂无摄像头。</p>';
      return;
    }
    list.innerHTML = `
      <table>
        <tr><th>ID</th><th>名称</th><th>类型</th><th>源地址</th><th>状态</th><th>操作</th></tr>
        ${cameras.map((c) => `
          <tr>
            <td class="mono">${c.id}</td>
            <td><input class="c-name" data-id="${c.id}" value="${c.name}"></td>
            <td>
              <select class="c-type" data-id="${c.id}">
                <option value="file"${c.source_type === 'file' ? ' selected' : ''}>视频文件</option>
                <option value="rtsp"${c.source_type === 'rtsp' ? ' selected' : ''}>RTSP 流</option>
              </select>
            </td>
            <td><input class="c-uri" data-id="${c.id}" size="36" value="${c.source_uri}"></td>
            <td><span class="badge ${c.status}">${c.status}</span></td>
            <td>
              ${c.status === 'running'
                ? `<button data-act="stop" data-id="${c.id}">停止</button>`
                : `<button data-act="start" data-id="${c.id}">启动</button>`}
              <button data-act="save" data-id="${c.id}">保存</button>
              <button class="danger" data-act="del" data-id="${c.id}">删除</button>
            </td>
          </tr>`).join('')}
      </table>`;
  }

  async function reload() {
    await reloadCameras();
    await reloadVideos();
  }

  el.querySelector('#c-create').onclick = async () => {
    try {
      await api('/cameras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: el.querySelector('#c-name').value || '未命名',
          source_type: el.querySelector('#c-type').value,
          source_uri: el.querySelector('#c-uri').value,
          autostart: el.querySelector('#c-autostart').checked,
        }),
      });
      toast('摄像头已添加');
      await reload();
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#list').onclick = async (ev) => {
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    const { act, id } = btn.dataset;
    try {
      if (act === 'del') {
        await api(`/cameras/${id}`, { method: 'DELETE' });
        toast('已删除');
      } else if (act === 'save') {
        const row = btn.closest('tr');
        await api(`/cameras/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: row.querySelector('.c-name').value,
            source_type: row.querySelector('.c-type').value,
            source_uri: row.querySelector('.c-uri').value,
          }),
        });
        toast('已保存');
      } else {
        await api(`/cameras/${id}/${act}`, { method: 'POST' });
        toast(act === 'start' ? '已启动' : '已停止');
      }
      await reloadCameras();
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#videos').onclick = async (ev) => {
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    if (btn.dataset.act !== 'vdel') return;
    try {
      await api(`/videos/${btn.dataset.id}`, { method: 'DELETE' });
      toast('视频已删除');
      await reloadVideos();
    } catch (err) { toast(err.message, true); }
  };

  await reload();
  return null;
}
