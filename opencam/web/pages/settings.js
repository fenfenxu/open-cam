// 设置：系统算力信息 + VLM 配置状态 + 平台账号状态 + 通知渠道管理
import { api, toast } from '../app.js';

export async function render(el) {
  el.innerHTML = '<h1>设置</h1><div id="sys" class="card"></div><div id="acct" class="card mt"></div><div id="notify" class="card mt"></div>';

  try {
    const info = await api('/api/system/info');
    el.querySelector('#sys').innerHTML = `
      <h3>系统信息</h3>
      <dl class="kv mt">
        <dt>版本</dt><dd>${info.version}</dd>
        <dt>推理设备</dt><dd>${info.device}（配置：${info.device_config}）</dd>
        <dt>系统内存</dt><dd>${info.memory_total_gb ?? '未知'} GB</dd>
        <dt>显存</dt><dd>${info.vram_total_gb ?? '—'} ${info.vram_total_gb ? 'GB' : ''}</dd>
        <dt>检测器</dt><dd>${info.detector}（${info.yolo_model}）</dd>
        <dt>采样帧率</dt><dd>${info.detect_fps} fps</dd>
        <dt>方案包</dt><dd>可用 ${info.packs_available} 个，其中已安装 ${info.packs_installed} 个</dd>
        <dt>VLM 复核</dt><dd>${info.vlm_configured
          ? `已配置（${info.vlm_model}）`
          : '未配置 OPENCAM_VLM_API_KEY，事件将标记为 skipped'}</dd>
      </dl>`;
  } catch (err) {
    el.querySelector('#sys').innerHTML = `<p class="dim">系统信息获取失败：${err.message}</p>`;
  }

  try {
    const acct = await api('/api/account/status');
    el.querySelector('#acct').innerHTML = `
      <h3>平台账号</h3>
      <dl class="kv mt">
        <dt>平台</dt><dd>${acct.platform_base_url || '未配置'}</dd>
        <dt>登录状态</dt><dd>${acct.logged_in ? '已登录' : '未登录'}</dd>
      </dl>
      ${acct.note ? `<p class="dim mt">${acct.note}</p>` : ''}`;
  } catch (err) {
    el.querySelector('#acct').innerHTML = `<p class="dim">账号状态获取失败：${err.message}</p>`;
  }

  // ---- 通知渠道 ----
  const notifyEl = el.querySelector('#notify');
  let channels = [];
  let cameras = [];
  try {
    [channels, cameras] = await Promise.all([
      api('/api/notify-channels'), api('/cameras')]);
  } catch (err) {
    notifyEl.innerHTML = `<p class="dim">通知渠道获取失败：${err.message}</p>`;
    return null;
  }

  const camName = (id) => {
    if (id == null) return '全部摄像头';
    const c = cameras.find((x) => x.id === id);
    return c ? `[${c.id}] ${c.name}` : `#${id}`;
  };
  const RULE_NAMES = {
    zone_intrusion: '区域入侵', loitering: '徘徊逗留', object_count: '人数统计',
    zone_count: '区域人数', line_crossing: '越线计数',
  };

  function renderChannels() {
    notifyEl.innerHTML = `
      <h3>通知渠道</h3>
      <p class="dim">事件命中后自动推送到 webhook（兼容飞书 / 企业微信 / 钉钉机器人）；适用范围留空表示全部。</p>
      ${channels.length === 0 ? '<p class="dim mt">还没有通知渠道。</p>' : `
        <table class="mt">
          <tr><th>名称</th><th>Webhook</th><th>适用范围</th><th>启用</th><th></th></tr>
          ${channels.map((ch) => `
            <tr>
              <td>${ch.name}</td>
              <td class="mono" style="max-width:260px;overflow:hidden;text-overflow:ellipsis">${ch.webhook}</td>
              <td>${camName(ch.camera_id)} · ${RULE_NAMES[ch.rule_type] || '全部类型'}</td>
              <td><input type="checkbox" data-toggle="${ch.id}" ${ch.enabled ? 'checked' : ''}></td>
              <td>
                <button data-test="${ch.id}">测试</button>
                <button class="danger" data-del="${ch.id}">删除</button>
              </td>
            </tr>`).join('')}
        </table>`}
      <div class="form-row mt">
        <input id="n-name" placeholder="联系人/渠道名" style="width:140px">
        <input id="n-webhook" placeholder="webhook URL" style="flex:1;min-width:220px">
        <select id="n-camera">
          <option value="">全部摄像头</option>
          ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${c.name}</option>`).join('')}
        </select>
        <select id="n-rule">
          <option value="">全部类型</option>
          ${Object.entries(RULE_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}
        </select>
        <button id="n-add">添加</button>
      </div>`;
  }

  notifyEl.onclick = async (ev) => {
    const t = ev.target;
    try {
      if (t.id === 'n-add') {
        const name = notifyEl.querySelector('#n-name').value.trim();
        const webhook = notifyEl.querySelector('#n-webhook').value.trim();
        if (!name || !webhook) { toast('请填写名称和 webhook URL', true); return; }
        await api('/api/notify-channels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name, webhook,
            camera_id: notifyEl.querySelector('#n-camera').value || null,
            rule_type: notifyEl.querySelector('#n-rule').value || null,
          }),
        });
        toast('已添加');
        channels = await api('/api/notify-channels');
        renderChannels();
      } else if (t.dataset.del) {
        await api(`/api/notify-channels/${t.dataset.del}`, { method: 'DELETE' });
        toast('已删除');
        channels = await api('/api/notify-channels');
        renderChannels();
      } else if (t.dataset.test) {
        const r = await api(`/api/notify-channels/${t.dataset.test}/test`, { method: 'POST' });
        toast(r.ok ? '测试推送成功' : `推送失败：${r.error}`, !r.ok);
      } else if (t.dataset.toggle) {
        await api(`/api/notify-channels/${t.dataset.toggle}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: t.checked }),
        });
      }
    } catch (err) { toast(err.message, true); }
  };

  renderChannels();
  return null;
}
