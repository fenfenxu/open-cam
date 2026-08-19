// 事件时间线：过滤 + 详情（快照/VLM 理由）+ 一键 ack + 误报/漏报飞轮
import { api, fmtTime, RULE_TYPE_NAMES, toast } from '../app.js';

export async function render(el) {
  const cameras = await api('/cameras');
  el.innerHTML = `
    <h1>事件</h1>
    <div class="form-row">
      <select id="f-camera">
        <option value="">全部摄像头</option>
        ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${c.name}</option>`).join('')}
      </select>
      <select id="f-type">
        <option value="">全部类型</option>
        <option value="zone_intrusion">区域入侵</option>
        <option value="loitering">徘徊滞留</option>
        <option value="object_count">数量超限</option>
      </select>
      <select id="f-verdict">
        <option value="">全部判定</option>
        <option value="confirmed">已确认</option>
        <option value="false_alarm">误报</option>
        <option value="uncertain">不确定</option>
      </select>
      <select id="f-acked">
        <option value="">全部状态</option>
        <option value="false">未确认</option>
        <option value="true">已确认</option>
      </select>
      <button id="f-reload">刷新</button>
    </div>
    <div class="mt" id="list"></div>
    <div class="mt event-detail" id="detail"></div>
  `;

  async function reload() {
    const params = new URLSearchParams();
    const cam = el.querySelector('#f-camera').value;
    const type = el.querySelector('#f-type').value;
    const verdict = el.querySelector('#f-verdict').value;
    const acked = el.querySelector('#f-acked').value;
    if (cam) params.set('camera_id', cam);
    if (type) params.set('rule_type', type);
    if (verdict) params.set('vlm_verdict', verdict);
    if (acked) params.set('acked', acked);
    params.set('limit', '100');

    const events = await api(`/events?${params}`);
    const list = el.querySelector('#list');
    if (events.length === 0) {
      list.innerHTML = '<p class="dim">没有匹配的事件。</p>';
      return;
    }
    list.innerHTML = `
      <table>
        <tr><th>时间</th><th>摄像头</th><th>类型</th><th>置信度</th>
            <th>VLM 判定</th><th>状态</th><th></th></tr>
        ${events.map((e) => `
          <tr data-id="${e.id}" style="cursor:pointer">
            <td class="mono">${fmtTime(e.ts)}</td>
            <td class="mono">${e.camera_id}</td>
            <td>${RULE_TYPE_NAMES[e.type] || e.type}</td>
            <td class="mono">${e.confidence.toFixed(2)}</td>
            <td><span class="badge ${e.vlm_verdict || e.vlm_status}">
              ${e.vlm_verdict || e.vlm_status}</span></td>
            <td>${e.acked ? '已确认' : '未确认'}</td>
            <td>${e.acked ? '' : `<button data-ack="${e.id}">ack</button>`}</td>
          </tr>`).join('')}
      </table>`;
  }

  async function fillFeedbackTasks(select) {
    try {
      const tasks = (await api('/training/tasks')).filter((t) => t.status === 'confirmed');
      if (!tasks.length) {
        select.innerHTML = '<option value="">没有已确认的训练任务</option>';
        return;
      }
      select.innerHTML = tasks.map((t) =>
        `<option value="${t.task_id}">${t.object} · ${t.property} (${t.task_id})</option>`).join('');
    } catch {
      select.innerHTML = '<option value="">无法加载训练任务</option>';
    }
  }

  async function renderDetail(e) {
    el.querySelector('#detail').innerHTML = `
      <h2>事件 #${e.id}</h2>
      ${e.snapshot_path ? `<img src="/events/${e.id}/snapshot" alt="快照">` : ''}
      <dl class="kv mt">
        <dt>时间</dt><dd>${fmtTime(e.ts)}</dd>
        <dt>类型</dt><dd>${RULE_TYPE_NAMES[e.type] || e.type}</dd>
        <dt>置信度</dt><dd>${e.confidence}</dd>
        <dt>详情</dt><dd>${JSON.stringify(e.detail)}</dd>
        <dt>VLM 状态</dt><dd>${e.vlm_status}</dd>
        <dt>VLM 判定</dt><dd>${e.vlm_verdict || '—'}</dd>
        <dt>VLM 理由</dt><dd>${e.vlm_reason || '—'}</dd>
      </dl>
      <div class="card mt" id="fb-box">
        <h3>训练反馈</h3>
        <p class="dim">误报/漏报样本会自动进入对应任务的数据集，下次训练会用上。</p>
        <div class="form-row">
          <select id="fb-task"><option value="">加载中…</option></select>
        </div>
        <div class="form-row">
          <button data-fb="false_alarm">这是误报</button>
          <button data-fb="miss">这是漏报</button>
        </div>
      </div>`;
    await fillFeedbackTasks(el.querySelector('#fb-task'));
    el.querySelector('#fb-box').onclick = async (ev) => {
      const btn = ev.target.closest('[data-fb]');
      if (!btn) return;
      const taskId = el.querySelector('#fb-task').value;
      if (!taskId) { toast('请先选一个训练任务', true); return; }
      try {
        await api(`/events/${e.id}/feedback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, kind: btn.dataset.fb }),
        });
        toast(btn.dataset.fb === 'miss' ? '已记为漏报并入库' : '已记为误报并入库');
        await reload();
      } catch (err) { toast(err.message, true); }
    };
  }

  el.querySelector('#list').onclick = async (ev) => {
    const ackBtn = ev.target.closest('button[data-ack]');
    if (ackBtn) {
      ev.stopPropagation();
      try {
        await api(`/events/${ackBtn.dataset.ack}/ack`, { method: 'POST' });
        toast('已确认');
        await reload();
      } catch (err) { toast(err.message, true); }
      return;
    }
    const row = ev.target.closest('tr[data-id]');
    if (!row) return;
    try {
      const e = await api(`/events/${row.dataset.id}`);
      await renderDetail(e);
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#f-reload').onclick = reload;
  await reload();
  return null;
}
