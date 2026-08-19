// 方案市场：浏览内置/已安装包，一键应用、安装（路径/URL）、卸载
import { api, RULE_TYPE_NAMES, toast } from '../app.js';

function uninstallBtn(p) {
  return p.origin === 'installed'
    ? `<button class="danger" data-uninstall="${p.id}">卸载</button>` : '';
}

function newPackBody(p) {
  const rows = (p.cameras || []).map((c) => {
    const names = (p.rules || [])
      .filter((r) => r.camera === c.id)
      .map((r) => r.name)
      .join('、');
    return `<li>${c.name}${names ? `：${names}` : ''}</li>`;
  }).join('');
  return `
        <ul class="meta mt">${rows}</ul>
        <div class="form-row mt">
          <button data-apply="${p.id}">应用</button>
          ${uninstallBtn(p)}
        </div>`;
}

function legacyPackBody(p, existing) {
  return `
        <div class="meta mt">规则模板：${p.rules.map((r) => r.name).join('、')}</div>
        <div class="form-row mt">
          <select data-cam-for="${p.id}">
            ${existing.map((c) => `<option value="${c.id}">应用到：[${c.id}] ${c.name}</option>`).join('')}
          </select>
          <button data-apply="${p.id}" ${existing.length ? '' : 'disabled'}>应用</button>
          ${uninstallBtn(p)}
        </div>`;
}

export async function render(el) {
  el.innerHTML = `
    <h1>方案市场</h1>
    <div class="card">
      <div class="form-row">
        <label>安装源</label>
        <input id="install-src" size="50"
          placeholder="本地目录 / pack.zip 路径 / https://... 包地址">
        <button id="install-btn">安装</button>
      </div>
      <div class="dim" id="online-note"></div>
    </div>
    <div class="card-grid mt" id="pack-grid"></div>
  `;

  try {
    const online = await api('/api/packs/online');
    el.querySelector('#online-note').textContent = online.note || '';
  } catch { /* 忽略 */ }

  async function reload() {
    const [packs, existing] = await Promise.all([
      api('/api/packs'),
      api('/cameras'),
    ]);
    const grid = el.querySelector('#pack-grid');
    grid.innerHTML = packs.map((p) => `
      <div class="card">
        <h3>${p.name} <span class="badge">${p.origin === 'builtin' ? '内置' : '已安装'}</span></h3>
        <div class="meta">${p.vertical} · v${p.version} · ${p.author || '匿名'}</div>
        <p>${p.description}</p>
        ${p.cameras == null ? legacyPackBody(p, existing) : newPackBody(p)}
      </div>`).join('');
  }

  el.querySelector('#install-btn').onclick = async () => {
    const source = el.querySelector('#install-src').value.trim();
    if (!source) { toast('请填写安装源', true); return; }
    try {
      const pack = await api('/api/packs/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source }),
      });
      toast(`已安装：${pack.name}`);
      await reload();
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#pack-grid').onclick = async (ev) => {
    const applyBtn = ev.target.closest('button[data-apply]');
    const unBtn = ev.target.closest('button[data-uninstall]');
    try {
      if (applyBtn) {
        const packId = applyBtn.dataset.apply;
        const camSelect = el.querySelector(`select[data-cam-for="${packId}"]`);
        const body = camSelect ? { camera_id: Number(camSelect.value) } : {};
        const data = await api(`/api/packs/${packId}/apply`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (camSelect) {
          toast(`已应用 ${data.rules.length} 条规则（含：${data.rules.map((r) => RULE_TYPE_NAMES[r.type]).join('、')}），可到「规则」页调整`);
        } else {
          toast(`已创建 ${data.cameras.length} 路摄像头，请到「摄像头」页改成真实源后再启动`);
        }
        await reload();
      } else if (unBtn) {
        await api(`/api/packs/${unBtn.dataset.uninstall}`, { method: 'DELETE' });
        toast('已卸载');
        await reload();
      }
    } catch (err) { toast(err.message, true); }
  };

  await reload();
  return null;
}
