// 设置：系统算力 + 大模型配置 + 平台账号 + 通知渠道
import { api, toast } from '../app.js';

const VLM_PRESETS = [
  { id: 'zhipu', name: '智谱 GLM（推荐，有免费档）',
    base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4v-flash' },
  { id: 'qwen', name: '通义千问',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-vl-plus' },
  { id: 'kimi', name: 'Kimi',
    base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k-vision-preview' },
  { id: 'custom', name: '自定义 OpenAI 兼容接口', base_url: '', model: '' },
];

function matchPreset(baseUrl) {
  return VLM_PRESETS.find((p) => p.base_url && p.base_url === baseUrl)?.id || 'custom';
}

export async function render(el) {
  el.innerHTML = '<h1>设置</h1><div id="sys" class="card"></div><div id="vlm" class="card mt"></div><div id="acct" class="card mt"></div><div id="notify" class="card mt"></div>';

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
        <dt>数据目录</dt><dd class="mono">${info.data_dir || ''}</dd>
      </dl>`;
  } catch (err) {
    el.querySelector('#sys').innerHTML = `<p class="dim">系统信息获取失败：${err.message}</p>`;
  }

  const vlmEl = el.querySelector('#vlm');
  async function renderVlm() {
    let vlm;
    try {
      vlm = await api('/api/system/vlm');
    } catch (err) {
      vlmEl.innerHTML = `<p class="dim">大模型配置读取失败：${err.message}</p>`;
      return;
    }
    const preset = matchPreset(vlm.base_url);
    const status = vlm.configured
      ? `已配置${vlm.api_key_hint ? `（${vlm.api_key_hint}）` : ''}，来源：${vlm.api_key_source === 'env' ? '环境变量' : '本页保存'}`
      : '未配置，训练解析和事件复核都需要它';
    vlmEl.innerHTML = `
      <h3>大模型</h3>
      <p class="dim">训练时理解你的需求、自动标注、告警复核都走这里。Key 只存在这台电脑的数据目录，不会进代码仓库。</p>
      ${vlm.env_locked ? '<p class="assist mt">当前生效的是环境变量 OPENCAM_VLM_API_KEY，本页保存的 Key 不会覆盖它。</p>' : ''}
      <p class="mt">${status}</p>
      <div class="form-row mt">
        <label>服务商</label>
        <select id="vlm-preset">${VLM_PRESETS.map((p) =>
          `<option value="${p.id}" ${p.id === preset ? 'selected' : ''}>${p.name}</option>`).join('')}
        </select>
      </div>
      <div class="form-row">
        <label>接口地址</label>
        <input id="vlm-url" style="flex:1;min-width:260px" value="${vlm.base_url || ''}">
      </div>
      <div class="form-row">
        <label>模型名</label>
        <input id="vlm-model" style="flex:1;min-width:220px" value="${vlm.model || ''}">
      </div>
      <div class="form-row">
        <label>API Key</label>
        <input id="vlm-key" type="password" autocomplete="off" style="flex:1;min-width:220px"
          placeholder="${vlm.api_key_hint ? '不改请留空，已保存 ' + vlm.api_key_hint : '粘贴 API Key'}"
          ${vlm.env_locked ? 'disabled' : ''}>
      </div>
      <div class="form-row mt">
        <button id="vlm-save">保存</button>
        <button id="vlm-test">测试连接</button>
        <button id="vlm-clear" class="danger" ${vlm.env_locked ? 'disabled' : ''}>清除本机 Key</button>
      </div>`;
    vlmEl.querySelector('#vlm-preset').onchange = (ev) => {
      const p = VLM_PRESETS.find((x) => x.id === ev.target.value);
      if (!p || p.id === 'custom') return;
      vlmEl.querySelector('#vlm-url').value = p.base_url;
      vlmEl.querySelector('#vlm-model').value = p.model;
    };
    vlmEl.querySelector('#vlm-save').onclick = async () => {
      const payload = {
        base_url: vlmEl.querySelector('#vlm-url').value.trim(),
        model: vlmEl.querySelector('#vlm-model').value.trim(),
      };
      const key = vlmEl.querySelector('#vlm-key').value.trim();
      if (key) payload.api_key = key;
      if (!payload.base_url || !payload.model) { toast('请填写接口地址和模型名', true); return; }
      if (!key && !vlm.configured) { toast('请填写 API Key', true); return; }
      try {
        await api('/api/system/vlm', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast('已保存');
        await renderVlm();
      } catch (err) { toast(err.message, true); }
    };
    vlmEl.querySelector('#vlm-test').onclick = async () => {
      try {
        const r = await api('/api/system/vlm/test', { method: 'POST' });
        toast(r.ok ? `连接成功（${r.model}）` : '测试失败');
      } catch (err) { toast(err.message, true); }
    };
    vlmEl.querySelector('#vlm-clear').onclick = async () => {
      try {
        await api('/api/system/vlm', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: '' }),
        });
        toast('已清除本机 Key');
        await renderVlm();
      } catch (err) { toast(err.message, true); }
    };
  }
  await renderVlm();

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
