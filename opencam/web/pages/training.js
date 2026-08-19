// 模型训练：七步向导（说需求 → 确认定义 → 选视频源抽帧 → 自动标注 →
// 人工确认 → 训练评估 → 部署/回滚）
import { api, toast } from '../app.js';

const STATUS_NAMES = {
  draft: '待确认定义', confirmed: '已确认定义', extracted: '已抽帧',
  labeling: '自动标注中', labeled: '标注完成', training: '训练中',
  trained: '训练完成', deployed: '已部署', failed: '失败',
};

const STEP_NAMES = ['说需求', '确认定义', '选视频源', '自动标注',
  '人工确认', '训练评估', '部署'];

export async function render(el) {
  el.innerHTML = '<h1>模型训练</h1><p class="dim">加载中…</p>';
  const cameras = await api('/cameras');
  let current = null; // 当前任务
  let polygon = [];   // 正在绘制的区域（0-1 相对坐标）
  let timer = null;

  function stepIndex(status) {
    return {
      draft: 1, confirmed: 2, extracted: 3, labeling: 3, labeled: 4,
      training: 5, trained: 5, deployed: 6, failed: 0,
    }[status] ?? 0;
  }

  async function refresh() {
    const tasks = await api('/api/training/tasks');
    el.querySelector('#task-list').innerHTML = tasks.length === 0
      ? '<p class="dim">还没有训练任务，先在下面描述你的需求。</p>'
      : `<table><tr><th>ID</th><th>名称</th><th>目标</th><th>状态</th></tr>
        ${tasks.map((t) => `
          <tr data-task="${t.id}" style="cursor:pointer">
            <td class="mono">${t.id}</td><td>${t.name}</td>
            <td class="dim">${t.goal}</td>
            <td><span class="badge">${STATUS_NAMES[t.status] || t.status}</span></td>
          </tr>`).join('')}</table>`;
  }

  async function loadTask(id) {
    current = await api(`/api/training/tasks/${id}`);
    polygon = current.polygon || [];
    renderWizard();
  }

  function renderWizard() {
    const t = current;
    const step = stepIndex(t.status);
    el.querySelector('#wizard').innerHTML = `
      <h2 class="mt">任务 #${t.id} · ${t.name}
        <span class="badge">${STATUS_NAMES[t.status] || t.status}</span></h2>
      <p class="dim">${STEP_NAMES.map((s, i) =>
        i === step ? `<b>[${s}]</b>` : s).join(' → ')}</p>
      ${t.error ? `<p class="badge error">错误：${t.error}</p>` : ''}

      <section class="card mt">
        <h3>1-2. 任务定义</h3>
        <p>目标：${t.goal}</p>
        <dl class="kv">
          <dt>对象</dt><dd><input id="d-object" value="${t.object_name}"></dd>
          <dt>属性</dt><dd><input id="d-property" value="${t.property_name}"></dd>
          <dt>类别</dt><dd><input id="d-classes" value="${t.classes.join(',')}">
            <span class="dim">逗号分隔，2-4 个互斥类别</span></dd>
          <dt>告警类别</dt><dd><input id="d-trigger"
            value="${t.rule.trigger_class || ''}"></dd>
          <dt>持续秒数</dt><dd><input id="d-duration" type="number"
            value="${t.rule.duration_s || 300}"></dd>
          <dt>指标</dt><dd class="dim">${t.metrics_explanation}</dd>
        </dl>
        ${t.status === 'draft' || t.status === 'confirmed'
          ? '<button id="btn-confirm-def">确认定义</button>' : ''}
      </section>

      <section class="card mt">
        <h3>3. 视频源与区域</h3>
        <div class="form-row">
          <select id="s-source">
            <option value="">选择摄像头</option>
            ${cameras.map((c) => `<option value="${c.id}"
              ${t.camera_id === c.id ? 'selected' : ''}>[${c.id}] ${c.name}</option>`).join('')}
          </select>
          <input id="s-video" placeholder="或视频文件路径"
                 value="${t.video_path || ''}">
          <button id="btn-full">全屏区域</button>
        </div>
        <p class="dim">在下方快照上点击绘制监控区域（至少 3 个点），
          右键撤销一个点。</p>
        <canvas id="roi" width="640" height="360"
                style="border:1px solid #444;max-width:100%"></canvas>
        <p class="dim">当前区域：${polygon.length} 个点</p>
        ${t.status === 'confirmed' || t.status === 'extracted'
          ? '<button id="btn-extract">开始抽帧</button>' : ''}
      </section>

      <section class="card mt">
        <h3>4-5. 标注与人工确认</h3>
        <p>样本统计：${Object.entries(t.sample_counts || {})
          .map(([k, v]) => `${k}: ${v}`).join(' · ') || '暂无'}</p>
        ${t.status === 'extracted'
          ? `<button id="btn-autolabel">开始自动标注</button>
             <span class="dim">未配置 VLM 时会提示，可直接人工确认</span>` : ''}
        ${t.labeling_running ? '<p class="dim">自动标注进行中…</p>' : ''}
        <div id="review" class="mt"></div>
      </section>

      <section class="card mt">
        <h3>6. 训练与评估</h3>
        ${t.training_running ? '<p class="dim">训练进行中…</p>' : ''}
        ${['labeled', 'trained', 'deployed'].includes(t.status)
          && !t.training_running
          ? '<button id="btn-train">开始训练</button>' : ''}
        <div id="report" class="mt"></div>
      </section>

      <section class="card mt">
        <h3>7. 部署 / 回滚</h3>
        <div class="form-row">
          <select id="deploy-camera">
            ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${c.name}</option>`).join('')}
          </select>
        </div>
        <div id="models" class="mt"></div>
      </section>`;

    bindWizard();
    drawRoi();
    loadReview();
    loadReport();
    loadModels();
  }

  function bindWizard() {
    const $ = (sel) => el.querySelector(sel);
    const on = (sel, fn) => { const n = $(sel); if (n) n.onclick = fn; };

    on('#btn-confirm-def', async () => {
      try {
        const classes = $('#d-classes').value.split(',').map((s) => s.trim())
          .filter(Boolean);
        await api(`/api/training/tasks/${current.id}/definition`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            object_name: $('#d-object').value,
            property_name: $('#d-property').value,
            classes,
            rule: { type: 'state_alert',
                    trigger_class: $('#d-trigger').value || classes.at(-1),
                    duration_s: Number($('#d-duration').value) || 300 },
            metrics: current.metrics,
          }),
        });
        toast('定义已确认');
        await loadTask(current.id);
        await refresh();
      } catch (err) { toast(err.message, true); }
    });

    on('#btn-full', () => {
      polygon = [[0, 0], [1, 0], [1, 1], [0, 1]];
      drawRoi();
    });

    on('#btn-extract', async () => {
      if (polygon.length < 3) { toast('请先绘制至少 3 个点的区域', true); return; }
      const camId = $('#s-source').value;
      const video = $('#s-video').value.trim();
      if (!camId && !video) { toast('请选择摄像头或填写视频路径', true); return; }
      try {
        await api(`/api/training/tasks/${current.id}/extract-frames`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            camera_id: camId ? Number(camId) : null,
            video_path: video || null,
            polygon, interval_s: 2, max_frames: 100,
          }),
        });
        toast('抽帧完成');
        await loadTask(current.id);
        await refresh();
      } catch (err) { toast(err.message, true); }
    });

    on('#btn-autolabel', async () => {
      try {
        await api(`/api/training/tasks/${current.id}/auto-label`, { method: 'POST' });
        toast('自动标注已开始');
        poll();
      } catch (err) { toast(err.message, true); }
    });

    on('#btn-train', async () => {
      try {
        await api(`/api/training/tasks/${current.id}/train`,
                  { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: '{}' });
        toast('训练已开始');
        poll();
      } catch (err) { toast(err.message, true); }
    });
  }

  // 在摄像头快照上点选多边形
  function drawRoi() {
    const canvas = el.querySelector('#roi');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const img = new Image();
    const camId = current.camera_id
      || el.querySelector('#s-source')?.value;
    img.onload = () => {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      if (polygon.length) {
        ctx.strokeStyle = '#4af';
        ctx.beginPath();
        polygon.forEach(([x, y], i) => {
          const px = x * canvas.width; const py = y * canvas.height;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        });
        if (polygon.length > 2) ctx.closePath();
        ctx.stroke();
      }
    };
    if (camId) img.src = `/cameras/${camId}/snapshot.jpg?ts=${Date.now()}`;
    else { ctx.fillStyle = '#222'; ctx.fillRect(0, 0, canvas.width, canvas.height); }

    canvas.onclick = (ev) => {
      const rect = canvas.getBoundingClientRect();
      polygon.push([(ev.clientX - rect.left) / rect.width,
                    (ev.clientY - rect.top) / rect.height]);
      drawRoi();
    };
    canvas.oncontextmenu = (ev) => { ev.preventDefault(); polygon.pop(); drawRoi(); };
  }

  async function loadReview() {
    const box = el.querySelector('#review');
    if (!box) return;
    const queue = await api(`/api/training/tasks/${current.id}/review`);
    if (!queue.length) { box.innerHTML = '<p class="dim">人工确认队列已清空。</p>'; return; }
    box.innerHTML = `
      <p>待确认 ${queue.length} 张（点类别确认，或跳过）：</p>
      <div style="display:flex;flex-wrap:wrap;gap:12px">
        ${queue.map((s) => `
          <div style="text-align:center">
            <img src="/api/training/tasks/${current.id}/samples/${s.id}/image"
                 width="128" style="display:block;border:1px solid #444">
            <div class="dim">VLM: ${s.vlm_label || '—'}
              ${s.vlm_confidence ? s.vlm_confidence.toFixed(2) : ''}</div>
            ${current.classes.map((c) => `
              <button data-sample="${s.id}" data-label="${c}">${c}</button>`).join('')}
            <button data-sample="${s.id}" data-label="skip">跳过</button>
          </div>`).join('')}
      </div>`;
    box.onclick = async (ev) => {
      const btn = ev.target.closest('button[data-sample]');
      if (!btn) return;
      try {
        await api(`/api/training/tasks/${current.id}/samples/${btn.dataset.sample}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label: btn.dataset.label }),
        });
        await loadTask(current.id);
      } catch (err) { toast(err.message, true); }
    };
  }

  async function loadReport() {
    const box = el.querySelector('#report');
    if (!box) return;
    try {
      const r = await api(`/api/training/tasks/${current.id}/report`);
      box.innerHTML = `
        <p><span class="badge ${r.passed ? 'confirmed' : 'error'}">
          ${r.passed ? '达标' : '未达标'}</span> ${r.conclusion}</p>
        <dl class="kv">
          <dt>准确率</dt><dd class="mono">${(r.metrics.accuracy * 100).toFixed(1)}%
            （目标 ${(r.targets.accuracy * 100).toFixed(0)}%）</dd>
          <dt>召回率</dt><dd class="mono">${(r.metrics.recall * 100).toFixed(1)}%
            （目标 ${(r.targets.recall * 100).toFixed(0)}%）</dd>
          <dt>误报率</dt><dd class="mono">
            ${(r.metrics.false_positive_rate * 100).toFixed(1)}%</dd>
          <dt>样本</dt><dd class="dim">训练 ${r.sample_counts.train} 张 /
            验证 ${r.sample_counts.val} 张</dd>
        </dl>`;
    } catch { box.innerHTML = '<p class="dim">训练完成后这里显示评估报告。</p>'; }
  }

  async function loadModels() {
    const box = el.querySelector('#models');
    if (!box) return;
    const models = await api(`/api/training/tasks/${current.id}/models`);
    if (!models.length) { box.innerHTML = '<p class="dim">还没有训练出的模型版本。</p>'; return; }
    box.innerHTML = `
      <table><tr><th>版本</th><th>准确率</th><th>状态</th><th></th></tr>
      ${models.map((m) => `
        <tr>
          <td class="mono">v${m.version}</td>
          <td class="mono">${m.metrics.accuracy != null
            ? (m.metrics.accuracy * 100).toFixed(1) + '%' : '—'}</td>
          <td><span class="badge">${{ trained: '待部署', deployed: '已部署',
            archived: '已下线' }[m.status] || m.status}</span></td>
          <td>
            ${m.status !== 'deployed'
              ? `<button data-deploy="${m.id}">部署</button>` : ''}
            ${m.status === 'deployed'
              ? `<button data-rollback="${m.id}">回滚</button>` : ''}
          </td>
        </tr>`).join('')}</table>`;
    box.onclick = async (ev) => {
      const deployBtn = ev.target.closest('button[data-deploy]');
      const rollbackBtn = ev.target.closest('button[data-rollback]');
      try {
        if (deployBtn) {
          const cameraId = Number(el.querySelector('#deploy-camera').value);
          if (!cameraId) { toast('请先添加摄像头', true); return; }
          await api(`/api/training/models/${deployBtn.dataset.deploy}/deploy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camera_id: cameraId }),
          });
          toast('已部署');
        } else if (rollbackBtn) {
          await api(`/api/training/models/${rollbackBtn.dataset.rollback}/rollback`,
                    { method: 'POST' });
          toast('已回滚');
        } else return;
        await loadTask(current.id);
        await refresh();
      } catch (err) { toast(err.message, true); }
    };
  }

  // 标注/训练期间轮询任务状态
  function poll() {
    if (timer) clearInterval(timer);
    timer = setInterval(async () => {
      try {
        const t = await api(`/api/training/tasks/${current.id}`);
        if (!t.labeling_running && !t.training_running) {
          clearInterval(timer); timer = null;
          await loadTask(current.id);
          await refresh();
        }
      } catch { /* 网络抖动忽略 */ }
    }, 2000);
  }

  // ---- 页面骨架 ----
  el.innerHTML = `
    <h1>模型训练</h1>
    <section class="card">
      <h2>新建任务</h2>
      <div class="form-row">
        <input id="new-goal" style="flex:1"
               placeholder="用一句话描述需求，如：垃圾桶快满了就提醒我">
        <button id="btn-create">创建任务</button>
      </div>
    </section>
    <section class="mt"><h2>任务列表</h2><div id="task-list"></div></section>
    <div id="wizard"></div>`;

  el.querySelector('#btn-create').onclick = async () => {
    const goal = el.querySelector('#new-goal').value.trim();
    if (!goal) { toast('请先描述你的需求', true); return; }
    try {
      const task = await api('/api/training/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal }),
      });
      toast('任务已创建，请确认定义');
      await refresh();
      await loadTask(task.id);
    } catch (err) { toast(err.message, true); }
  };

  el.querySelector('#task-list').onclick = async (ev) => {
    const row = ev.target.closest('tr[data-task]');
    if (row) await loadTask(Number(row.dataset.task));
  };

  await refresh();
  return () => { if (timer) clearInterval(timer); };
}
