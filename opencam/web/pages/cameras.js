// 摄像头管理：列表 + 新建 + 启停 + 删除
import { api, toast } from '../app.js';

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
    } catch (err) { toast(err.message, true); }
    fileInput.value = '';
  };

  async function reload() {
    const cameras = await api('/cameras');
    const list = el.querySelector('#list');
    if (cameras.length === 0) {
      list.innerHTML = '<p class="dim">暂无摄像头。</p>';
      return;
    }
    list.innerHTML = `
      <table>
        <tr><th>ID</th><th>名称</th><th>源</th><th>状态</th><th>操作</th></tr>
        ${cameras.map((c) => `
          <tr>
            <td class="mono">${c.id}</td>
            <td>${c.name}</td>
            <td class="mono">${c.source_type}:${c.source_uri}</td>
            <td><span class="badge ${c.status}">${c.status}</span></td>
            <td>
              ${c.status === 'running'
                ? `<button data-act="stop" data-id="${c.id}">停止</button>`
                : `<button data-act="start" data-id="${c.id}">启动</button>`}
              <button class="danger" data-act="del" data-id="${c.id}">删除</button>
            </td>
          </tr>`).join('')}
      </table>`;
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
      } else {
        await api(`/cameras/${id}/${act}`, { method: 'POST' });
        toast(act === 'start' ? '已启动' : '已停止');
      }
      await reload();
    } catch (err) { toast(err.message, true); }
  };

  await reload();
  return null;
}
