// 设置：系统算力信息 + VLM 配置状态 + 平台账号状态
import { api } from '../app.js';

export async function render(el) {
  el.innerHTML = '<h1>设置</h1><div id="sys" class="card"></div><div id="acct" class="card mt"></div>';

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
  return null;
}
