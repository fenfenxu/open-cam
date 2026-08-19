// 事件处置看板：过滤 + 星标/状态流转/负责人/备注编辑 + 处置时间线 + 重发通知
import { api, fmtTime, RULE_TYPE_NAMES, toast } from '../app.js';

const STATUS_NAMES = {
  logged: '观察',
  open: '待处理',
  acked: '已确认',
  resolved: '已处置',
  ignored: '已忽略',
};

const ACTION_NAMES = {
  star: '加关注',
  unstar: '取消关注',
  assign: '指派负责人',
  status: '状态流转',
  note: '备注',
  ack: '确认',
  notify: '通知推送',
};

// 各状态下可用的流转动作
const NEXT_ACTIONS = {
  open: [['acked', '确认'], ['resolved', '处置完成'], ['ignored', '误报忽略']],
  acked: [['resolved', '处置完成'], ['ignored', '误报忽略']],
  resolved: [['open', '重新打开']],
  ignored: [['open', '重新打开']],
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function fmtMediaTime(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '—';
  const s = Math.max(0, Number(sec));
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(2).padStart(5, '0');
  return `${String(m).padStart(2, '0')}:${rest}`;
}

function fmtClipRange(event) {
  if (event.source_offset == null) return '—';
  return `${fmtMediaTime(event.clip_start)} – ${fmtMediaTime(event.clip_end)}`;
}

function cameraLabel(e) {
  return e.camera_name || `#${e.camera_id}`;
}

function sourceLabel(e) {
  if (e.source_filename) return e.source_filename;
  return e.source_offset == null ? '直播流（无回放）' : '—';
}

export async function render(el) {
  const cameras = await api('/cameras');
  el.innerHTML = `
    <h1>待办</h1>
    <div class="form-row">
      <select id="f-camera">
        <option value="">全部摄像头</option>
        ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${esc(c.name)}</option>`).join('')}
      </select>
      <select id="f-type">
        <option value="">全部类型</option>
        ${Object.entries(RULE_TYPE_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}
      </select>
      <select id="f-status">
        <option value="">全部状态</option>
        ${Object.entries(STATUS_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}
      </select>
      <select id="f-verdict">
        <option value="">全部判定</option>
        <option value="confirmed">已确认</option>
        <option value="false_alarm">误报</option>
        <option value="uncertain">不确定</option>
      </select>
      <label><input type="checkbox" id="f-starred"> 仅看关注</label>
      <label class="dim"><input type="checkbox" id="f-include-obs"> 含观察记录</label>
      <button id="f-reload">刷新</button>
    </div>
    <div class="mt" id="list"></div>
    <div class="mt event-detail" id="detail"></div>
  `;

  async function reload() {
    const params = new URLSearchParams();
    const cam = el.querySelector('#f-camera').value;
    const type = el.querySelector('#f-type').value;
    const status = el.querySelector('#f-status').value;
    const verdict = el.querySelector('#f-verdict').value;
    if (cam) params.set('camera_id', cam);
    if (type) params.set('rule_type', type);
    if (status) params.set('status', status);
    if (verdict) params.set('vlm_verdict', verdict);
    if (el.querySelector('#f-starred').checked) params.set('starred', 'true');
    if (!el.querySelector('#f-include-obs').checked) params.set('needs_action', 'true');
    params.set('limit', '100');

    const events = await api(`/events?${params}`);
    const list = el.querySelector('#list');
    if (events.length === 0) {
      list.innerHTML = '<p class="dim">没有匹配的事件。</p>';
      return;
    }
    list.innerHTML = `
      <table>
        <tr><th></th><th>时间</th><th>摄像头</th><th>素材</th><th>类型</th><th>置信度</th>
            <th>VLM 判定</th><th>处置状态</th><th>负责人</th></tr>
        ${events.map((e) => `
          <tr data-id="${e.id}" style="cursor:pointer">
            <td><button class="star ${e.starred ? 'on' : ''}" data-star="${e.id}"
              data-on="${e.starred ? 1 : 0}" title="关注">${e.starred ? '★' : '☆'}</button></td>
            <td class="mono">${fmtTime(e.ts)}</td>
            <td>${esc(cameraLabel(e))}<div class="dim">${esc(sourceLabel(e))}</div></td>
            <td class="mono">${fmtClipRange(e)}</td>
            <td>${RULE_TYPE_NAMES[e.type] || e.type}</td>
            <td class="mono">${e.confidence.toFixed(2)}</td>
            <td><span class="badge ${e.vlm_verdict || e.vlm_status}">
              ${e.vlm_verdict || e.vlm_status}</span></td>
            <td><span class="badge st-${e.status}">${STATUS_NAMES[e.status] || e.status}</span></td>
            <td>${esc(e.assignee) || '—'}</td>
          </tr>`).join('')}
      </table>`;
  }

  let detailId = null;

  async function showDetail(id) {
    detailId = id;
    const [e, actions] = await Promise.all([
      api(`/events/${id}`),
      api(`/events/${id}/actions`),
    ]);
    const nextBtns = (e.needs_action === false ? [] : (NEXT_ACTIONS[e.status] || []))
      .map(([st, label]) => `<button data-status="${st}">${label}</button>`).join(' ');
    const range = fmtClipRange(e);
    const hasClip = e.source_offset != null;
    el.querySelector('#detail').innerHTML = `
      <h2>事件 #${e.id} <span class="badge st-${e.status}">${STATUS_NAMES[e.status] || e.status}</span></h2>
      <div class="event-media">
        ${e.snapshot_path ? `<img src="/events/${e.id}/snapshot" alt="快照">` : ''}
        <span class="time-badge">素材 ${range}${e.source_offset == null ? '' : `（命中 ${fmtMediaTime(e.source_offset)}）`}</span>
      </div>
      ${hasClip ? `<video class="event-clip" controls autoplay playsinline src="/events/${e.id}/clip"></video>` : '<p class="dim">该事件没有可回放的视频片段（实时流或升级前的旧数据只有快照）。</p>'}
      <dl class="kv mt">
        <dt>摄像头</dt><dd><a href="#/cameras/${e.camera_id}">${esc(cameraLabel(e))}</a></dd>
        <dt>视频</dt><dd>${esc(sourceLabel(e))}</dd>
        <dt>素材时段</dt><dd>${range}</dd>
        <dt>时间</dt><dd>${fmtTime(e.ts)}</dd>
        <dt>类型</dt><dd>${RULE_TYPE_NAMES[e.type] || e.type}</dd>
        <dt>置信度</dt><dd>${e.confidence}</dd>
        <dt>详情</dt><dd>${esc(JSON.stringify(e.detail))}</dd>
        <dt>VLM 状态</dt><dd>${e.vlm_status}</dd>
        <dt>VLM 判定</dt><dd>${e.vlm_verdict || '—'}</dd>
        <dt>VLM 理由</dt><dd>${esc(e.vlm_reason) || '—'}</dd>
      </dl>
      <h2>处置</h2>
      <div class="form-row">${nextBtns}
        <button id="d-renotify">重发通知</button></div>
      <div class="form-row">
        <label>负责人</label>
        <input id="d-assignee" value="${esc(e.assignee) || ''}" placeholder="处置负责人">
        <button id="d-save-assignee">保存</button>
      </div>
      <div class="form-row">
        <label>备注</label>
        <textarea id="d-note" rows="2" style="flex:1">${esc(e.note) || ''}</textarea>
        <button id="d-save-note">保存</button>
      </div>
      <h2>处置时间线</h2>
      <div id="d-actions">
        ${actions.length === 0 ? '<p class="dim">暂无处置记录。</p>' : `
          <table>
            <tr><th>时间</th><th>操作</th><th>操作者</th><th>细节</th></tr>
            ${actions.map((a) => `
              <tr>
                <td class="mono">${fmtTime(a.ts)}</td>
                <td>${ACTION_NAMES[a.action] || a.action}</td>
                <td>${esc(a.actor)}</td>
                <td class="mono">${esc(fmtPayload(a))}</td>
              </tr>`).join('')}
          </table>`}
      </div>
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
    bindClipPlayer(el.querySelector('#detail video.event-clip'), e);
  }

  function bindClipPlayer(video, event) {
    if (!video || event.source_offset == null) return;
    const start = event.clip_start;
    const end = event.clip_end;
    const isFullSource = () => video.duration > (end - start) + 1.5;
    video.addEventListener('loadedmetadata', () => {
      if (isFullSource()) video.currentTime = start;
    });
    video.addEventListener('timeupdate', () => {
      if (isFullSource() && video.currentTime >= end) video.pause();
    });
    video.addEventListener('error', () => {
      const hint = document.createElement('p');
      hint.className = 'dim';
      hint.textContent = '浏览器无法播放该素材格式，请查看上方带时段标注的快照。';
      video.replaceWith(hint);
    });
  }

  async function fillFeedbackTasks(select) {
    if (!select) return;
    try {
      const tasks = (await api('/training/tasks')).filter((t) => t.status === 'confirmed');
      if (!tasks.length) {
        select.innerHTML = '<option value="">没有已确认的训练任务</option>';
        return;
      }
      select.innerHTML = tasks.map((t) =>
        `<option value="${t.task_id}">${esc(t.object)} · ${esc(t.property)} (${esc(t.task_id)})</option>`).join('');
    } catch {
      select.innerHTML = '<option value="">无法加载训练任务</option>';
    }
  }

  function fmtPayload(a) {
    const p = a.payload || {};
    if (a.action === 'notify') return p.ok ? '推送成功' : `失败：${p.error || ''}`;
    if (a.action === 'status') return `${STATUS_NAMES[p.from] || p.from} → ${STATUS_NAMES[p.to] || p.to}`;
    if (a.action === 'assign') return `→ ${p.to || '（取消指派）'}`;
    if (a.action === 'note') return p.text || '';
    return '';
  }

  async function patch(id, body, msg) {
    try {
      await api(`/events/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast(msg);
      await reload();
      await showDetail(id);
    } catch (err) { toast(err.message, true); }
  }

  el.querySelector('#list').onclick = async (ev) => {
    const starBtn = ev.target.closest('button[data-star]');
    if (starBtn) {
      ev.stopPropagation();
      try {
        await api(`/events/${starBtn.dataset.star}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ starred: starBtn.dataset.on !== '1' }),
        });
        await reload();
      } catch (err) { toast(err.message, true); }
      return;
    }
    const row = ev.target.closest('tr[data-id]');
    if (!row) return;
    try {
      await showDetail(row.dataset.id);
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#detail').onclick = async (ev) => {
    const id = detailId;
    if (!id) return;
    const statusBtn = ev.target.closest('button[data-status]');
    if (statusBtn) {
      await patch(id, { status: statusBtn.dataset.status }, '状态已更新');
      return;
    }
    if (ev.target.id === 'd-save-assignee') {
      await patch(id, { assignee: el.querySelector('#d-assignee').value || null }, '负责人已保存');
      return;
    }
    if (ev.target.id === 'd-save-note') {
      await patch(id, { note: el.querySelector('#d-note').value || null }, '备注已保存');
      return;
    }
    if (ev.target.id === 'd-renotify') {
      try {
        await api(`/events/${id}/notify`, { method: 'POST' });
        toast('已提交重发，稍后查看处置时间线');
        setTimeout(() => showDetail(id).catch(() => {}), 1500);
      } catch (err) { toast(err.message, true); }
      return;
    }
    const fb = ev.target.closest('[data-fb]');
    if (fb) {
      const taskId = el.querySelector('#fb-task')?.value;
      if (!taskId) { toast('请先选一个训练任务', true); return; }
      try {
        await api(`/events/${id}/feedback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, kind: fb.dataset.fb }),
        });
        toast(fb.dataset.fb === 'miss' ? '已记为漏报并入库' : '已记为误报并入库');
        await reload();
        await showDetail(id);
      } catch (err) { toast(err.message, true); }
    }
  };

  el.querySelector('#f-reload').onclick = reload;
  el.querySelector('#f-starred').onchange = reload;
  el.querySelector('#f-include-obs').onchange = reload;
  await reload();
  return null;
}
