// 规则配置（场景引导式）：
// ① 选场景卡片 → ② 填参数（带默认值与中文提示）→ ③ 需要区域的进画布画多边形
import { api, RULE_TYPE_NAMES, toast } from '../app.js';

const PRESET_CARD_CLASS = {
  zone_intrusion: 'p-intrusion',
  loitering: 'p-loitering',
  object_count: 'p-count',
  zone_count: 'p-zonecount',
  line_crossing: 'p-line',
};

const DIRECTION_NAMES = { both: '双向', in: '仅进', out: '仅出' };

export async function render(el) {
  const cameras = await api('/cameras');
  const presetData = await api('/api/rules/presets');
  const presets = presetData.presets;
  const commonClasses = presetData.common_classes;

  el.innerHTML = `
    <h1>规则</h1>
    <div class="form-row">
      <label>摄像头</label>
      <select id="cam-select">
        ${cameras.map((c) => `<option value="${c.id}">[${c.id}] ${c.name}</option>`).join('')}
      </select>
      <span class="dim" id="cam-hint"></span>
    </div>

    <div class="step-bar" id="step-bar" hidden>
      <span class="step" data-step="1">① 选场景</span>
      <span class="step" data-step="2">② 填参数</span>
      <span class="step" data-step="3">③ 画区域</span>
    </div>

    <div id="step-1" hidden>
      <p class="dim">这条规则想解决什么问题？选一个最接近的场景：</p>
      <div class="preset-grid mt" id="preset-grid"></div>
    </div>

    <div class="card" id="step-2" hidden>
      <div id="field-rows"></div>
      <div class="form-row mt">
        <button id="to-step-3">下一步</button>
        <button id="back-1" class="danger">返回重选</button>
      </div>
    </div>

    <div class="card" id="step-3" hidden>
      <div id="roi-wrap"><canvas id="roi-canvas"></canvas></div>
      <div class="form-row mt">
        <button id="r-close" disabled>闭合多边形</button>
        <button id="r-undo" disabled>撤销点</button>
        <button id="r-save" disabled>保存规则</button>
        <button id="back-2" class="danger">返回改参数</button>
      </div>
      <p class="dim" id="roi-hint">在画布上点击添加顶点，至少 3 个点；双击画布也可闭合。</p>
    </div>

    <h2>已有规则</h2>
    <div id="rule-list"></div>
  `;

  if (cameras.length === 0) {
    el.querySelector('#cam-hint').textContent = '请先添加摄像头';
    return null;
  }

  // ---- 状态 ----
  let preset = null;       // 选中的预设
  let values = {};         // 表单值
  let img = null;
  let scale = 1;
  let points = [];
  let rules = [];

  const canvas = el.querySelector('#roi-canvas');
  const ctx = canvas.getContext('2d');
  const sel = el.querySelector('#cam-select');

  // ---- 步骤切换 ----
  function showStep(n) {
    el.querySelector('#step-bar').hidden = false;
    for (const s of [1, 2, 3]) {
      el.querySelector(`#step-${s}`).hidden = s !== n;
    }
    el.querySelectorAll('.step-bar .step').forEach((s) => {
      const sn = Number(s.dataset.step);
      s.classList.toggle('active', sn === n);
      // 不需要画区域的规则没有第 3 步
      if (sn === 3 && preset && !preset.needs_zone) s.style.display = 'none';
      else s.style.display = '';
    });
  }

  // ---- 第 1 步：场景卡片 ----
  el.querySelector('#preset-grid').innerHTML = presets.map((p) => `
    <div class="preset-card ${PRESET_CARD_CLASS[p.type]}" data-type="${p.type}">
      <h3>${p.display_name}</h3>
      <div class="tagline">${p.tagline}</div>
      <div class="desc">${p.description}</div>
      <ul>${p.scenarios.map((s) => `<li>${s}</li>`).join('')}</ul>
    </div>`).join('');

  el.querySelector('#preset-grid').onclick = (ev) => {
    const card = ev.target.closest('.preset-card');
    if (!card) return;
    preset = presets.find((p) => p.type === card.dataset.type);
    values = {};
    for (const f of preset.fields) values[f.key] = f.default;
    renderFields();
    showStep(2);
  };

  // ---- 第 2 步：参数表单 ----
  function renderFields() {
    el.querySelector('#field-rows').innerHTML = preset.fields.map((f) => {
      if (f.kind === 'class') {
        return `
          <div class="form-row">
            <label>${f.label}</label>
            <select data-field="${f.key}">
              ${commonClasses.map((c) => `<option value="${c.id}"
                ${f.default.includes(c.id) ? 'selected' : ''}>${c.name}（${c.id}）</option>`).join('')}
            </select>
            <span class="dim">${f.hint}；${presetData.classes_note}</span>
          </div>`;
      }
      if (f.kind === 'direction') {
        return `
          <div class="form-row">
            <label>${f.label}</label>
            <select data-field="${f.key}">
              ${Object.entries(DIRECTION_NAMES).map(([v, n]) =>
                `<option value="${v}" ${f.default === v ? 'selected' : ''}>${n}</option>`).join('')}
            </select>
            <span class="dim">${f.hint}（沿线第一点→第二点看，左→右为进）</span>
          </div>`;
      }
      return `
        <div class="form-row">
          <label>${f.label}</label>
          <input data-field="${f.key}"
            type="${f.kind === 'number' ? 'number' : 'text'}" value="${f.default}">
          ${f.unit ? `<span class="dim">${f.unit}</span>` : ''}
          <span class="dim">${f.hint}</span>
        </div>`;
    }).join('');
  }

  function collectValues() {
    el.querySelectorAll('[data-field]').forEach((input) => {
      const f = preset.fields.find((x) => x.key === input.dataset.field);
      if (f.kind === 'number') values[f.key] = Number(input.value) || f.default;
      else if (f.kind === 'class') values[f.key] = [input.value];
      else values[f.key] = input.value.trim() || f.default;
    });
  }

  el.querySelector('#back-1').onclick = () => showStep(1);
  el.querySelector('#back-2').onclick = () => showStep(2);

  el.querySelector('#to-step-3').onclick = async () => {
    collectValues();
    if (preset.needs_zone) {
      el.querySelector('#roi-hint').textContent = preset.zone_shape === 'line'
        ? '在画布上点击两个点画出计数线；再次点击可重画。方向约定：沿第一点→第二点看，左→右为进。'
        : '在画布上点击添加顶点，至少 3 个点；双击画布也可闭合。';
      if (!await loadCanvas()) return; // 无画面时画不了区域
      showStep(3);
    } else {
      await saveRule();
    }
  };

  // ---- 第 3 步：画布画多边形 ----
  async function loadCanvas() {
    const camId = sel.value;
    return new Promise((resolve) => {
      img = new Image();
      img.onload = () => {
        const maxW = Math.min(900, el.clientWidth - 40);
        scale = Math.min(1, maxW / img.naturalWidth);
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        points = [];
        redraw();
        updateButtons();
        resolve(true);
      };
      img.onerror = () => {
        toast('该摄像头暂无画面（未启动？），先启动摄像头再画区域', true);
        resolve(false);
      };
      img.src = `/cameras/${camId}/snapshot.jpg?t=${Date.now()}`;
    });
  }

  function drawShape(pts, color, closed, isLine) {
    if (pts.length === 0) return;
    ctx.strokeStyle = color;
    ctx.fillStyle = color + '33';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pts[0][0] * scale, pts[0][1] * scale);
    for (const [x, y] of pts.slice(1)) ctx.lineTo(x * scale, y * scale);
    if (closed && !isLine) { ctx.closePath(); ctx.fill(); }
    ctx.stroke();
    for (const [x, y] of pts) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x * scale, y * scale, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function isLineMode() {
    return preset && preset.zone_shape === 'line';
  }

  function minPoints() {
    return isLineMode() ? 2 : 3;
  }

  function redraw() {
    if (!img) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    for (const rule of rules) {
      const poly = rule.params && rule.params.polygon;
      const line = rule.params && rule.params.line;
      if (poly) drawShape(poly, '#3b9eff', true, false);
      if (line) drawShape(line, '#34c77b', false, true);
    }
    drawShape(points, '#e5b545', !isLineMode(), isLineMode());
  }

  function updateButtons() {
    el.querySelector('#r-close').disabled = points.length < minPoints();
    el.querySelector('#r-undo').disabled = points.length === 0;
    // 线模式恰好 2 点；多边形至少 3 点
    el.querySelector('#r-save').disabled = isLineMode()
      ? points.length !== 2 : points.length < 3;
    el.querySelector('#r-close').textContent = isLineMode() ? '完成线段' : '闭合多边形';
  }

  canvas.onclick = (ev) => {
    if (isLineMode() && points.length >= 2) points = []; // 画线时重新点击即重画
    const rect = canvas.getBoundingClientRect();
    points.push([
      Math.round((ev.clientX - rect.left) / scale),
      Math.round((ev.clientY - rect.top) / scale),
    ]);
    redraw();
    updateButtons();
  };
  canvas.ondblclick = () => {
    if (points.length >= minPoints()) toast(isLineMode() ? '线段已完成' : `多边形已闭合（${points.length} 个顶点）`);
  };
  el.querySelector('#r-close').onclick = () => {
    toast(isLineMode() ? '线段已完成' : `多边形已闭合（${points.length} 个顶点）`);
  };
  el.querySelector('#r-undo').onclick = () => { points.pop(); redraw(); updateButtons(); };
  el.querySelector('#r-save').onclick = saveRule;

  // ---- 保存 ----
  function buildParams() {
    const params = {};
    if (preset.needs_zone) {
      if (preset.zone_shape === 'line') params.line = points;
      else params.polygon = points;
    }
    for (const f of preset.fields) {
      if (f.key === 'name' || f.key === 'cooldown') continue;
      if (f.key === 'active_hours') {
        if (values.active_hours) params.active_hours = values.active_hours;
        continue;
      }
      if (f.key === 'classes') {
        if (preset.type === 'object_count') params.class = values.classes[0];
        else params.classes = values.classes;
      } else {
        params[f.key] = values[f.key];
      }
    }
    return params;
  }

  async function saveRule() {
    try {
      await api(`/cameras/${sel.value}/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: values.name,
          type: preset.type,
          params: buildParams(),
          cooldown: Number(values.cooldown) || 30,
        }),
      });
      toast(`规则「${values.name}」已保存`);
      points = [];
      showStep(1);
      await loadRules();
    } catch (err) { toast(err.message, true); }
  }

  // ---- 规则列表 ----
  function paramSummary(rule) {
    const p = rule.params || {};
    const parts = [];
    if (rule.type === 'loitering' && p.duration) parts.push(`停留超 ${p.duration} 秒`);
    if (rule.type === 'object_count' && p.threshold) parts.push(`数量超 ${p.threshold} 个`);
    if (rule.type === 'zone_count' && p.threshold) parts.push(`区域内超 ${p.threshold} 人`);
    if (rule.type === 'line_crossing') {
      parts.push(`穿越方向：${DIRECTION_NAMES[p.direction || 'both']}`);
    }
    const cls = p.class || (p.classes || []).join('/');
    if (cls) parts.push(`目标：${cls}`);
    if (p.polygon) parts.push(`${p.polygon.length} 边形区域`);
    if (p.line) parts.push('一条线段');
    if (p.active_hours) parts.push(`${p.active_hours} 生效`);
    return parts.join(' · ');
  }

  async function loadRules() {
    rules = await api(`/cameras/${sel.value}/rules`);
    const list = el.querySelector('#rule-list');
    if (rules.length === 0) {
      list.innerHTML = '<p class="dim">该摄像头还没有规则，从上面的场景卡片开始吧。</p>';
      return;
    }
    list.innerHTML = `
      <table>
        <tr><th>规则</th><th>类型</th><th>说明</th><th>冷却</th><th>启用</th><th></th></tr>
        ${rules.map((r) => `
          <tr>
            <td>${r.name || RULE_TYPE_NAMES[r.type]}</td>
            <td><span class="type-tag ${r.type}">${RULE_TYPE_NAMES[r.type] || r.type}</span></td>
            <td class="dim">${paramSummary(r)}</td>
            <td class="mono">${r.cooldown}s</td>
            <td>${r.enabled ? '是' : '否'}</td>
            <td><button class="danger" data-del="${r.id}">删除</button></td>
          </tr>`).join('')}
      </table>`;
  }

  el.querySelector('#rule-list').onclick = async (ev) => {
    const btn = ev.target.closest('button[data-del]');
    if (!btn) return;
    try {
      await api(`/cameras/${sel.value}/rules/${btn.dataset.del}`, { method: 'DELETE' });
      toast('规则已删除');
      await loadRules();
    } catch (err) { toast(err.message, true); }
  };

  sel.onchange = async () => { points = []; await loadRules(); showStep(1); };

  await loadRules();
  showStep(1);
  return null;
}
