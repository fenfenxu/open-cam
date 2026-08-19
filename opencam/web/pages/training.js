// 模型训练向导：按七步旅程（需求 → 定义 → 视频源 → 标注 → 训练 → 评估 → 部署）
import { api, toast } from '../app.js';

const STEPS = [
  { id: 1, title: '说需求' },
  { id: 2, title: '确认定义' },
  { id: 3, title: '选视频源' },
  { id: 4, title: '自动标注' },
  { id: 5, title: '训练' },
  { id: 6, title: '评估' },
  { id: 7, title: '部署' },
];

function jsonHeaders(body) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function inferStep(task) {
  if (!task) return 1;
  const train = task.train || {};
  if (train.status === 'done' || train.result) return 6;
  if (train.status === 'running') return 5;
  if ((task.samples?.review || 0) > 0 || (task.samples?.total || 0) > 0) return 4;
  if ((task.frames || 0) > 0) return 3;
  if (task.status === 'confirmed') return 3;
  return 2;
}

export async function render(el, ctx = {}) {
  const cameras = await api('/cameras');
  const videos = await api('/videos').catch(() => []);
  let tasks = await api('/training/tasks');
  let taskId = ctx.id || null;
  let task = null;
  let step = 1;
  let pollTimer = null;

  if (taskId) {
    try {
      task = await api(`/training/tasks/${taskId}`);
      step = inferStep(task);
    } catch {
      taskId = null;
    }
  }

  el.innerHTML = `
    <h1>模型训练</h1>
    <p class="dim">用一句话描述想监控的状态，系统帮你抽帧、标注、训练并部署本地小模型。</p>
    <div class="card mt" id="task-picker">
      <div class="form-row">
        <label>已有任务</label>
        <select id="task-select">
          <option value="">新任务</option>
        </select>
        <button id="task-load">打开</button>
      </div>
    </div>
    <div class="step-bar mt" id="wiz-bar"></div>
    <div class="card" id="wiz-body"></div>
  `;

  const sel = el.querySelector('#task-select');
  function fillTasks(list) {
    sel.innerHTML = '<option value="">新任务</option>' + list.map((t) =>
      `<option value="${t.task_id}">${t.object || t.task_id} · ${t.property || t.status}</option>`).join('');
    if (taskId) sel.value = taskId;
  }
  fillTasks(tasks);

  function setBar() {
    el.querySelector('#wiz-bar').innerHTML = STEPS.map((s) =>
      `<span class="step${s.id === step ? ' active' : ''}" data-goto="${s.id}">${s.id}. ${s.title}</span>`).join('');
  }

  async function refreshTask() {
    if (!taskId) return;
    task = await api(`/training/tasks/${taskId}`);
    location.hash = `#/training/${taskId}`;
  }

  async function show() {
    setBar();
    const body = el.querySelector('#wiz-body');
    if (step === 1) {
      let vlmHint = '';
      try {
        const vlm = await api('/api/system/vlm');
        if (!vlm.configured) {
          vlmHint = '<p class="assist error mt">还没配置大模型。<a href="#/settings">去设置页填写接口和 API Key</a>，否则系统无法真正理解你写的需求。</p>';
        }
      } catch { /* 设置接口失败不挡向导 */ }
      body.innerHTML = `
        <h3>① 说需求</h3>
        <p class="dim">例如：「垃圾桶快满了就提醒我」</p>
        ${vlmHint}
        <textarea id="goal" rows="3" style="width:100%">${task?.goal || ''}</textarea>
        <div class="form-row mt"><button id="go-define">生成任务定义</button></div>`;
      body.querySelector('#go-define').onclick = async () => {
        const goal = body.querySelector('#goal').value.trim();
        if (!goal) { toast('请先写一句需求', true); return; }
        try {
          const created = await api('/training/tasks', jsonHeaders({ goal, task_id: taskId || undefined }));
          taskId = created.task_id;
          task = created;
          tasks = await api('/training/tasks');
          fillTasks(tasks);
          location.hash = `#/training/${taskId}`;
          step = 2;
          await show();
        } catch (err) { toast(err.message, true); }
      };
      return;
    }

    if (!taskId) {
      body.innerHTML = '<p class="dim">请先完成第一步。</p>';
      return;
    }

    if (step === 2) {
      const d = task.definition || {};
      const classes = (d.classes || []).join(', ');
      body.innerHTML = `
        <h3>② 确认定义</h3>
        <p class="dim">${task.metrics_explained || ''}</p>
        <dl class="kv mt">
          <dt>对象</dt><dd><input id="d-object" value="${d.object || ''}"></dd>
          <dt>属性</dt><dd><input id="d-property" value="${d.property || ''}"></dd>
          <dt>类别（逗号分隔）</dt><dd><input id="d-classes" value="${classes}"></dd>
          <dt>告警触发</dt><dd><input id="d-trigger" value="${(d.rule && d.rule.trigger) || ''}"></dd>
        </dl>
        <div class="form-row mt">
          <button id="confirm-def">确认并进入下一步</button>
          <button id="back-1" class="danger">返回改需求</button>
        </div>`;
      body.querySelector('#back-1').onclick = () => { step = 1; show(); };
      body.querySelector('#confirm-def').onclick = async () => {
        const definition = {
          object: body.querySelector('#d-object').value.trim(),
          property: body.querySelector('#d-property').value.trim(),
          classes: body.querySelector('#d-classes').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
          rule: { type: 'state_alert', trigger: body.querySelector('#d-trigger').value.trim() },
          metrics: d.metrics,
          region: d.region,
          goal: task.goal,
        };
        try {
          await api(`/training/tasks/${taskId}/confirm`, jsonHeaders({ definition }));
          await refreshTask();
          step = 3;
          await show();
        } catch (err) { toast(err.message, true); }
      };
      return;
    }

    if (step === 3) {
      body.innerHTML = `
        <h3>③ 选视频源并抽帧</h3>
        <div class="form-row">
          <label>摄像头</label>
          <select id="src-cam">
            <option value="">—</option>
            ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${c.name}</option>`).join('')}
          </select>
          <label>或视频库</label>
          <select id="src-vid">
            <option value="">—</option>
            ${videos.map((v) => `<option value="${v.id}">${v.filename}</option>`).join('')}
          </select>
          <button id="do-frames">抽帧</button>
        </div>
        <p class="dim" id="frame-hint">已抽 ${task.frames || 0} 张。抽帧后可在画面上点出垃圾桶所在区域。</p>
        <div id="roi-wrap"><canvas id="roi-canvas"></canvas></div>
        <div class="form-row mt">
          <button id="save-region" ${task.frames ? '' : 'disabled'}>保存区域</button>
          <button id="to-4">下一步：自动标注</button>
          <button id="back-2" class="danger">返回</button>
        </div>`;
      body.querySelector('#back-2').onclick = () => { step = 2; show(); };
      body.querySelector('#do-frames').onclick = async () => {
        const cam = body.querySelector('#src-cam').value;
        const vid = body.querySelector('#src-vid').value;
        const payload = cam ? { camera_id: Number(cam) } : (vid ? { video_id: Number(vid) } : null);
        if (!payload) { toast('请选择摄像头或视频', true); return; }
        try {
          const r = await api(`/training/tasks/${taskId}/frames`, jsonHeaders(payload));
          toast(`抽了 ${r.written ?? 0} 帧`);
          await refreshTask();
          await show();
        } catch (err) { toast(err.message, true); }
      };
      const points = [];
      const canvas = body.querySelector('#roi-canvas');
      const ctx = canvas.getContext('2d');
      const img = new Image();
      img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
      };
      if (task.frames) img.src = `/training/tasks/${taskId}/preview.jpg?t=${Date.now()}`;
      canvas.onclick = (ev) => {
        const r = canvas.getBoundingClientRect();
        const x = (ev.clientX - r.left) * (canvas.width / r.width);
        const y = (ev.clientY - r.top) * (canvas.height / r.height);
        points.push([x, y]);
        ctx.drawImage(img, 0, 0);
        ctx.strokeStyle = '#3b9eff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        points.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
        ctx.stroke();
      };
      body.querySelector('#save-region').onclick = async () => {
        if (points.length < 3) { toast('至少点 3 个顶点', true); return; }
        try {
          await api(`/training/tasks/${taskId}/region`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ region: points }),
          });
          toast('区域已保存');
          await refreshTask();
        } catch (err) { toast(err.message, true); }
      };
      body.querySelector('#to-4').onclick = () => { step = 4; show(); };
      return;
    }

    if (step === 4) {
      body.innerHTML = `
        <h3>④ 自动标注 + 人工确认</h3>
        <p class="dim">高置信样本自动入库；不确定的只需点类别或跳过。</p>
        <div class="form-row">
          <button id="do-ann">开始标注</button>
          <span class="dim" id="ann-stat"></span>
        </div>
        <div id="review-box" class="mt"></div>
        <div class="form-row mt">
          <button id="to-5">下一步：训练</button>
          <button id="back-3" class="danger">返回</button>
        </div>`;
      body.querySelector('#back-3').onclick = () => { step = 3; show(); };
      async function loadReview() {
        const q = await api(`/training/tasks/${taskId}/review`);
        const box = body.querySelector('#review-box');
        body.querySelector('#ann-stat').textContent =
          `待确认 ${q.remaining} 张`;
        if (!q.items.length) {
          box.innerHTML = '<p class="dim">队列已空，可以去训练。</p>';
          return;
        }
        const item = q.items[0];
        box.innerHTML = `
          <img class="event-detail" src="/training/tasks/${taskId}/crop/${item.id}.jpg" alt="裁剪">
          <p class="dim">建议：${item.suggested_label || '无'}（${item.confidence.toFixed(2)}） ${item.reason || ''}</p>
          <div class="form-row">
            ${item.classes.map((c) => `<button data-label="${c}">${c}</button>`).join('')}
            <button class="danger" data-skip="1">跳过</button>
          </div>`;
        box.onclick = async (ev) => {
          const skip = ev.target.closest('[data-skip]');
          const btn = ev.target.closest('[data-label]');
          if (!skip && !btn) return;
          try {
            await api(`/training/tasks/${taskId}/review/${item.id}`, jsonHeaders(
              skip ? { action: 'skip' } : { action: 'confirm', label: btn.dataset.label }));
            await loadReview();
          } catch (err) { toast(err.message, true); }
        };
      }
      body.querySelector('#do-ann').onclick = async () => {
        try {
          const r = await api(`/training/tasks/${taskId}/annotate`, { method: 'POST' });
          toast(`自动 ${r.auto}，待确认 ${r.review}`);
          await refreshTask();
          await loadReview();
        } catch (err) { toast(err.message, true); }
      };
      await loadReview();
      body.querySelector('#to-5').onclick = () => { step = 5; show(); };
      return;
    }

    if (step === 5) {
      body.innerHTML = `
        <h3>⑤ 训练</h3>
        <p class="dim">从预训练 YOLO 微调固定区域分类。训练在后台执行。</p>
        <div class="form-row">
          <button id="do-train">开始训练</button>
          <span class="dim" id="train-stat"></span>
        </div>
        <div class="form-row mt">
          <button id="to-6">查看评估</button>
          <button id="back-4" class="danger">返回</button>
        </div>`;
      body.querySelector('#back-4').onclick = () => { step = 4; show(); };
      const stat = body.querySelector('#train-stat');
      async function poll() {
        const s = await api(`/training/tasks/${taskId}/train`);
        stat.textContent = s.status + (s.error ? ` · ${s.error}` : '');
        if (s.status === 'done') {
          await refreshTask();
          step = 6;
          await show();
          return;
        }
        if (s.status === 'failed') toast(s.error || '训练失败', true);
        if (s.status === 'running') pollTimer = setTimeout(poll, 1500);
      }
      body.querySelector('#do-train').onclick = async () => {
        try {
          await api(`/training/tasks/${taskId}/train`, jsonHeaders({ epochs: 20 }));
          toast('已开始训练');
          await poll();
        } catch (err) { toast(err.message, true); }
      };
      body.querySelector('#to-6').onclick = () => { step = 6; show(); };
      const cur = task.train || {};
      if (cur.status === 'running') poll();
      else stat.textContent = cur.status || 'idle';
      return;
    }

    if (step === 6) {
      const report = (task.train && task.train.result) || {};
      body.innerHTML = `
        <h3>⑥ 评估报告</h3>
        <p>${report.conclusion || '还没有评估报告，请先完成训练。'}</p>
        <pre class="dim mt" style="white-space:pre-wrap">${(report.suggestions || []).map((s) => '· ' + s).join('\n')}</pre>
        <div class="form-row mt">
          <button id="to-7">去部署</button>
          <button id="back-5" class="danger">返回</button>
        </div>`;
      body.querySelector('#back-5').onclick = () => { step = 5; show(); };
      body.querySelector('#to-7').onclick = () => { step = 7; show(); };
      return;
    }

    if (step === 7) {
      const models = await api(`/models?task_id=${encodeURIComponent(taskId)}`).catch(() => []);
      body.innerHTML = `
        <h3>⑦ 一键部署 / 回滚</h3>
        <p class="dim">登记本任务最新模型，与线上指标对比后再替换；回滚入口常驻。</p>
        <div class="form-row">
          <button id="do-reg">登记并部署</button>
          <button id="do-force" class="danger">强制部署</button>
        </div>
        <div id="model-list" class="mt"></div>
        <div class="form-row mt"><button id="back-6" class="danger">返回</button></div>`;
      body.querySelector('#back-6').onclick = () => { step = 6; show(); };
      const list = body.querySelector('#model-list');
      list.innerHTML = models.length
        ? `<table><tr><th>id</th><th>槽位</th><th>状态</th><th></th></tr>${
          models.map((m) => `<tr>
            <td class="mono">${m.id}</td><td>${m.slot_key}</td>
            <td><span class="badge">${m.status}</span></td>
            <td>${m.status === 'live'
              ? `<button data-rb="${m.id}">回滚</button>`
              : `<button data-dp="${m.id}">部署</button>`}</td>
          </tr>`).join('')}</table>`
        : '<p class="dim">还没有登记模型。</p>';
      async function deploy(id, force) {
        try {
          const r = await api(`/models/${id}/deploy`, jsonHeaders({ force: !!force }));
          toast(r.reason || '已部署');
          await refreshTask();
          await show();
        } catch (err) { toast(err.message, true); }
      }
      body.querySelector('#do-reg').onclick = async () => {
        try {
          const m = await api('/models', jsonHeaders({ task_id: taskId }));
          await deploy(m.id, false);
        } catch (err) { toast(err.message, true); }
      };
      body.querySelector('#do-force').onclick = async () => {
        try {
          const m = await api('/models', jsonHeaders({ task_id: taskId }));
          await deploy(m.id, true);
        } catch (err) { toast(err.message, true); }
      };
      list.onclick = async (ev) => {
        const rb = ev.target.closest('[data-rb]');
        const dp = ev.target.closest('[data-dp]');
        try {
          if (rb) {
            const r = await api(`/models/${rb.dataset.rb}/rollback`, { method: 'POST' });
            toast(r.reason || '已回滚');
            await show();
          } else if (dp) await deploy(dp.dataset.dp, false);
        } catch (err) { toast(err.message, true); }
      };
    }
  }

  el.querySelector('#task-load').onclick = async () => {
    taskId = sel.value || null;
    if (!taskId) { task = null; step = 1; location.hash = '#/training'; await show(); return; }
    await refreshTask();
    step = inferStep(task);
    await show();
  };
  el.querySelector('#wiz-bar').onclick = (ev) => {
    const s = ev.target.closest('[data-goto]');
    if (!s) return;
    step = Number(s.dataset.goto);
    show();
  };

  await show();
  return () => { if (pollTimer) clearTimeout(pollTimer); };
}
