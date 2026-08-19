# 摄像头直播预览与文件回放 Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-camera-live-and-file-replay-design.md`。REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans。从 `origin/main` 开分支。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。

**Goal:** 摄像头详情页能持续看采集画面（MJPEG）；仅本地文件源提供可拖进度的回放；RTSP 不回放、本机不录像。

**Architecture:** 直播从已有 `CaptureWorker` 环形缓冲 `latest_frame()` 编码 JPEG，经 `GET /cameras/{id}/live.mjpg` 推 multipart。回放只对 `source_type=file` 用 `FileResponse` 返回该摄像头自己的源文件（`clip.resolve_source_uri`），浏览器 `<video>` seek。控制台 hash 增加 `#/cameras/{id}`。

**Tech Stack:** FastAPI StreamingResponse / FileResponse、OpenCV `imencode`、无构建原生 HTML/JS、pytest TestClient + mock detector。

## Global Constraints

- 规格原文：`docs/superpowers/specs/2026-08-19-camera-live-and-file-replay-design.md`（错误文案、HTTP 码以规格为准，逐字使用）。
- 基线：`origin/main`。不要把工作区里未提交的 Alembic / doctor / Makefile RTSP 改动卷进本 PR，除非它们已在 main。
- Python ≥ 3.12；`from __future__ import annotations`；用户可见文案中文，标识符英文。
- **禁止**本机录像、环形 mp4、给 RTSP 编告警短片、ONVIF/HLS/WebRTC。
- **禁止**改 CLI、检测流水线、规则、事件 clip 窗口。
- 测试用 `tmp_settings`，`OPENCAM_DETECTOR=mock`，不下载模型、不依赖网络。
- 改 API 后必须 `uv run python scripts/export_openapi.py`。
- 命令：`uv run pytest`。验证合同：规格「测试」表必须全部落地为 pytest。

## 范围边界

做：live.mjpg、source、详情页、仪表盘/列表入口、openapi、README 一句说明、对应测试。  
不做：录像、RTSP 回放、CLI、事件 clip 行为变更、直播与文件播放器时间轴对齐。

## 验收标准（DoD）

1. 运行中摄像头 `GET /cameras/{id}/live.mjpg` 为 200，`Content-Type` 含 `multipart/x-mixed-replace`，流里出现 JPEG SOI `\xff\xd8`。
2. 未运行或无帧：live.mjpg 503，detail `暂无可用帧（摄像头未运行或流未就绪）`。
3. 不存在的 id：live.mjpg 与 source 均为 404，detail `摄像头不存在`。
4. `source_type=rtsp` 的 `GET .../source` 为 400，detail `该源为直播流，不支持文件回放`。
5. `source_type=file` 且磁盘上有小 mp4：source 200，content-type 含 `video`，body 非空。
6. file 但文件缺失：source 404，detail `源文件不存在`。
7. Web：`#/cameras/{id}` 详情含直播 `<img src=".../live.mjpg">`；file 有 `<video src=".../source">`；rtsp 含文案 `该源为直播流，不支持回放`。仪表盘卡片可点进详情。
8. `uv run pytest` 全绿；`docs/openapi.json` 含 `/cameras/{camera_id}/live.mjpg` 与 `/cameras/{camera_id}/source`。

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | API：`source` + `live.mjpg` | `tests/test_camera_live_replay.py` 全绿 |
| 2 | Web：路由 + 详情页 + 入口 | `test_web.py` 含 camera.js / live.mjpg / 回放文案 |
| 3 | OpenAPI + README | `docs/openapi.json` 含新路径；README 有接口说明 |

---

### Task 1: live.mjpg 与 source API

**Files:**
- Create: `tests/test_camera_live_replay.py`
- Modify: `opencam/api/cameras.py`（文件末尾 snapshot 之后追加两个路由）
- Test: `uv run pytest tests/test_camera_live_replay.py -v`

**Interfaces:**
- Consumes: `camera_manager.latest_frame` / `get` / `is_running`（已有）；`clip.resolve_source_uri`、`clip.media_type_for`（已有）；`Camera.status`、`CAMERA_RUNNING`。
- Produces: `GET /cameras/{camera_id}/live.mjpg`、`GET /cameras/{camera_id}/source`。

- [ ] **Step 1: 写失败测试**

`tests/test_camera_live_replay.py` 全文：

```python
"""摄像头直播 MJPEG 与文件源回放。"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _write_tiny_mp4(path: Path, frames: int = 30, fps: int = 10) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (160, 120))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(frame, (i * 4, 40), (i * 4 + 20, 80), (0, 255, 0), -1)
        writer.write(frame)
    writer.release()


def test_missing_camera_live_and_source(client):
    assert client.get("/cameras/999/live.mjpg").status_code == 404
    assert "摄像头不存在" in client.get("/cameras/999/live.mjpg").json()["detail"]
    assert client.get("/cameras/999/source").status_code == 404
    assert "摄像头不存在" in client.get("/cameras/999/source").json()["detail"]


def test_stopped_file_camera_live_is_503(client):
    cid = client.post("/cameras", json={
        "name": "停", "source_type": "file", "source_uri": "/tmp/x.mp4",
    }).json()["id"]
    resp = client.get(f"/cameras/{cid}/live.mjpg")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "暂无可用帧（摄像头未运行或流未就绪）"


def test_rtsp_source_rejected(client):
    cid = client.post("/cameras", json={
        "name": "流", "source_type": "rtsp",
        "source_uri": "rtsp://127.0.0.1:8554/test",
    }).json()["id"]
    resp = client.get(f"/cameras/{cid}/source")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "该源为直播流，不支持文件回放"


def test_file_source_serves_mp4(client, tmp_path):
    video = tmp_path / "scene.mp4"
    _write_tiny_mp4(video)
    cid = client.post("/cameras", json={
        "name": "文件", "source_type": "file", "source_uri": str(video),
    }).json()["id"]
    resp = client.get(f"/cameras/{cid}/source")
    assert resp.status_code == 200, resp.text
    assert "video" in resp.headers["content-type"]
    assert len(resp.content) > 100


def test_missing_file_source_404(client):
    cid = client.post("/cameras", json={
        "name": "缺", "source_type": "file",
        "source_uri": "/tmp/opencam-no-such.mp4",
    }).json()["id"]
    resp = client.get(f"/cameras/{cid}/source")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "源文件不存在"


def test_running_camera_live_mjpeg_has_jpeg(client, tmp_path):
    video = tmp_path / "live.mp4"
    _write_tiny_mp4(video, frames=60, fps=10)
    cid = client.post("/cameras", json={
        "name": "播", "source_type": "file", "source_uri": str(video),
        "autostart": True,
    }).json()["id"]
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = client.get(f"/cameras/{cid}/snapshot.jpg")
            if snap.status_code == 200:
                break
            time.sleep(0.2)
        else:
            pytest.fail("启动后 5s 内没有快照帧")
        with client.stream("GET", f"/cameras/{cid}/live.mjpg") as resp:
            assert resp.status_code == 200
            assert "multipart/x-mixed-replace" in resp.headers["content-type"]
            data = b""
            for chunk in resp.iter_bytes():
                data += chunk
                if b"\xff\xd8" in data:
                    break
            else:
                pytest.fail("MJPEG 流中未出现 JPEG")
    finally:
        client.post(f"/cameras/{cid}/stop")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_camera_live_replay.py -v`

Expected: FAIL（路由 404 或未定义）

- [ ] **Step 3: 实现两个端点**

在 `opencam/api/cameras.py` 增加 import：

```python
import time

from fastapi.responses import FileResponse, StreamingResponse

from ..clip import media_type_for, resolve_source_uri
from ..streams.manager import camera_manager  # 已有则不要重复
```

（文件顶部已有 `camera_manager`、`Response`、`HTTPException`。只补 `time`、`FileResponse`、`StreamingResponse`、`clip` 两个函数。）

在 `snapshot` 函数**之后**追加（boundary 名必须是 `frame`）：

```python
_MJPEG_BOUNDARY = "frame"
_MJPEG_INTERVAL = 0.125


def _iter_mjpeg(camera_id: int):
    """从采集缓冲持续吐 JPEG part；摄像头停止或客户端断开则结束。"""
    while True:
        worker = camera_manager.get(camera_id)
        if worker is None or not worker.is_alive():
            return
        frame = camera_manager.latest_frame(camera_id)
        if frame is not None:
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                payload = buf.tobytes()
                yield (
                    b"--" + _MJPEG_BOUNDARY.encode()
                    + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(payload)).encode()
                    + b"\r\n\r\n" + payload + b"\r\n"
                )
        time.sleep(_MJPEG_INTERVAL)


@router.get("/{camera_id}/live.mjpg", summary="实时 MJPEG 预览",
            description="从采集缓冲约 8fps 推 JPEG。未运行或无帧时 503，不建立长连接。")
def live_mjpeg(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status != CAMERA_RUNNING or camera_manager.latest_frame(camera_id) is None:
        raise HTTPException(503, "暂无可用帧（摄像头未运行或流未就绪）")
    return StreamingResponse(
        _iter_mjpeg(camera_id),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY}",
    )


@router.get("/{camera_id}/source", summary="文件源原片（供回放）",
            description="仅 source_type=file；直播流 400。支持 HTTP Range。")
def camera_source(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.source_type != "file":
        raise HTTPException(400, "该源为直播流，不支持文件回放")
    path = resolve_source_uri(camera.source_uri)
    if not path.is_file():
        raise HTTPException(404, "源文件不存在")
    return FileResponse(
        path, media_type=media_type_for(path), filename=path.name)
```

模块 docstring 改为：`摄像头管理 API：CRUD + 启停 + 实时抓帧 + MJPEG 预览 + 文件源回放 + 视频文件上传。`

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_camera_live_replay.py tests/test_events_api.py::test_camera_not_found_and_snapshot_unavailable -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_camera_live_replay.py opencam/api/cameras.py
git commit -m "feat: add camera MJPEG live preview and file source replay API"
```

---

### Task 2: 详情页与入口

**Files:**
- Create: `opencam/web/pages/camera.js`
- Modify: `opencam/web/app.js`（分段 hash；详情时侧栏「摄像头」高亮）
- Modify: `opencam/web/pages/dashboard.js`（整卡可点）
- Modify: `opencam/web/pages/cameras.js`（查看按钮；启停/删除 `stopPropagation`）
- Modify: `opencam/web/style.css`（`.cam-live` / `.cam-replay` / `.card.cam-link`）
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 的 `/live.mjpg` 与 `/source`；现有 `GET /cameras/{id}`、`POST .../start|stop`。
- Produces: `#/cameras/{id}` 详情页；`parseHash()` 返回 `{ page, id, sidebar }`。

- [ ] **Step 1: 写失败的 Web 冒烟**

在 `tests/test_web.py` 的 `test_static_assets` 路径列表加上 `"/static/pages/camera.js"`。

追加：

```python
def test_camera_detail_live_and_replay_copy(client):
    js = client.get("/static/pages/camera.js").text
    assert "/live.mjpg" in js
    assert "/source" in js
    assert "该源为直播流，不支持回放" in js
    app = client.get("/static/app.js").text
    assert "cameras/" in app
    dash = client.get("/static/pages/dashboard.js").text
    assert "#/cameras/" in dash
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_web.py::test_camera_detail_live_and_replay_copy tests/test_web.py::test_static_assets -v`

Expected: FAIL（缺少 camera.js 或文案）

- [ ] **Step 3: 改 `app.js` 路由**

把 `render()` 换成可解析 `#/cameras/12`。保留 `api` / `toast` / `fmtTime` / `RULE_TYPE_NAMES` 不变。`routes` 增加 `camera`：

```javascript
const routes = {
  dashboard: () => import('./pages/dashboard.js'),
  cameras: () => import('./pages/cameras.js'),
  camera: () => import('./pages/camera.js'),
  rules: () => import('./pages/rules.js'),
  events: () => import('./pages/events.js'),
  marketplace: () => import('./pages/marketplace.js'),
  settings: () => import('./pages/settings.js'),
};

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '') || 'dashboard';
  const parts = raw.split('/').filter(Boolean);
  if (parts[0] === 'cameras' && parts[1] && /^\d+$/.test(parts[1])) {
    return { page: 'camera', id: Number(parts[1]), sidebar: 'cameras' };
  }
  const page = routes[parts[0]] ? parts[0] : 'dashboard';
  return { page, id: null, sidebar: page };
}

async function render() {
  const { page, id, sidebar } = parseHash();
  document.querySelectorAll('#sidebar nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === sidebar);
  });
  if (cleanup) { cleanup(); cleanup = null; }
  const app = document.getElementById('app');
  app.innerHTML = '';
  try {
    const mod = await routes[page]();
    cleanup = await mod.render(app, { id }) || null;
  } catch (err) {
    app.innerHTML = `<h1>页面加载失败</h1><p class="dim">${err.message}</p>`;
  }
}
```

现有各页 `render(el)` 忽略第二参数即可，不必改签名。

- [ ] **Step 4: 新建 `opencam/web/pages/camera.js`**

```javascript
// 摄像头详情：MJPEG 直播；文件源可回放，RTSP 不可
import { api, toast } from '../app.js';

export async function render(el, ctx = {}) {
  const id = ctx.id;
  if (!id) {
    el.innerHTML = '<p class="dim">缺少摄像头 id</p>';
    return null;
  }
  const cam = await api(`/cameras/${id}`);
  const running = cam.status === 'running';
  const isFile = cam.source_type === 'file';
  const live = running
    ? `<img class="cam-live" alt="直播" src="/cameras/${id}/live.mjpg">`
    : `<p class="dim">摄像头未运行。<img class="cam-shot" alt="暂无画面" src="/cameras/${id}/snapshot.jpg" onerror="this.style.display='none'"></p>`;
  const replay = isFile
    ? `<video class="cam-replay" controls playsinline src="/cameras/${id}/source"></video>`
    : '<p class="dim">该源为直播流，不支持回放。</p>';

  el.innerHTML = `
    <p class="meta"><a href="#/cameras">← 摄像头列表</a></p>
    <h1>${cam.name} <span class="badge ${cam.status}">${cam.status}</span></h1>
    <div class="meta mono">${cam.source_type} · ${cam.source_uri}</div>
    <div class="mt">
      ${running
        ? `<button data-act="stop">停止</button>`
        : `<button data-act="start">启动</button>`}
    </div>
    <h2 class="mt">直播</h2>
    ${live}
    <h2 class="mt">回放</h2>
    ${replay}
  `;

  const video = el.querySelector('video.cam-replay');
  if (video) {
    video.addEventListener('error', () => {
      const hint = document.createElement('p');
      hint.className = 'dim';
      hint.textContent = '浏览器无法播放该格式。';
      video.replaceWith(hint);
    });
  }

  el.querySelector('[data-act]').onclick = async (ev) => {
    const act = ev.target.dataset.act;
    try {
      await api(`/cameras/${id}/${act}`, { method: 'POST' });
      toast(act === 'start' ? '已启动' : '已停止');
      await render(el, ctx);
    } catch (err) { toast(err.message, true); }
  };

  return () => {
    const img = el.querySelector('img.cam-live');
    if (img) img.src = '';
  };
}
```

注意：启停后再次 `render` 会替换 DOM；先执行 cleanup 里断 MJPEG 再重绘。上面直接 `await render(el, ctx)` 时旧 img 会被 innerHTML 清掉。仍要在页面离开时 `img.src=''`。

- [ ] **Step 5: 仪表盘整卡可点**

`dashboard.js` 在 `grid.appendChild(card)` 之前：

```javascript
    card.classList.add('cam-link');
    card.addEventListener('click', () => {
      location.hash = `#/cameras/${cam.id}`;
    });
```

- [ ] **Step 6: 摄像头列表「查看」**

表头操作列旁，每行增加：

```html
<button data-act="view" data-id="${c.id}">查看</button>
```

在现有 `el.querySelector('#list').onclick` 里，`del/start/stop` 之前：

```javascript
    if (act === 'view') {
      location.hash = `#/cameras/${id}`;
      return;
    }
```

按钮已是 `data-act`，行点击不必做。

- [ ] **Step 7: 样式**

在 `.cam-shot` 后追加：

```css
.card.cam-link { cursor: pointer; }
.card.cam-link:hover { border-color: var(--accent); }
.cam-live, .cam-replay {
  width: 100%;
  max-width: 960px;
  background: #000;
  border-radius: 6px;
  border: 1px solid var(--border);
  margin: 8px 0;
  display: block;
}
.cam-live { aspect-ratio: 16/9; object-fit: contain; }
```

- [ ] **Step 8: 跑 Web 测试**

Run: `uv run pytest tests/test_web.py tests/test_camera_live_replay.py -v`

Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add opencam/web/app.js opencam/web/pages/camera.js opencam/web/pages/dashboard.js \
  opencam/web/pages/cameras.js opencam/web/style.css tests/test_web.py
git commit -m "feat: add camera detail live preview and file replay page"
```

---

### Task 3: OpenAPI 与 README

**Files:**
- Modify: `docs/openapi.json`（生成，不要手改）
- Modify: `README.md`（「运行」节后补接口说明；当前 README **没有** API 表，不要编造表格）

- [ ] **Step 1: 导出 OpenAPI**

Run: `uv run python scripts/export_openapi.py`

确认 `docs/openapi.json` 的 `paths` 含 `/cameras/{camera_id}/live.mjpg` 与 `/cameras/{camera_id}/source`。

可加最小断言到 `tests/test_web.py` 或现有 openapi 测试（若仓库已有 `tests/test_openapi_cameras.py` 则把两路径加进去；没有则不要新建大文件，Task 1 的 HTTP 测试已覆盖行为）。

- [ ] **Step 2: README**

在「运行」代码块之后追加：

```markdown
控制台点仪表盘卡片进入摄像头详情：运行中显示 MJPEG 直播。仅视频文件源可在详情页拖进度回放；RTSP 直播不支持回放（不会在本机录像）。

- `GET /cameras/{id}/live.mjpg` 实时预览
- `GET /cameras/{id}/source` 文件源原片（RTSP 返回 400）
```

- [ ] **Step 3: 全量测试**

Run: `uv run pytest`

Expected: PASS（本文件撰写时基线约 74+；新文件应增加 Task 1 的 6 个用例和 Task 2 的 Web 断言）

- [ ] **Step 4: Commit**

```bash
git add docs/openapi.json README.md tests/test_web.py
git commit -m "docs: document camera live MJPEG and file replay endpoints"
```

---

## Spec 覆盖核对

| 规格条目 | 任务 |
|---|---|
| live.mjpg 404/503/multipart/8fps/quality 80/停则结束 | Task 1 |
| source 404/400/FileResponse/Range/只用本摄像头 uri | Task 1 |
| `#/cameras/{id}`、侧栏高亮、camera.js、cleanup 断流 | Task 2 |
| 仪表盘可点、列表查看 | Task 2 |
| RTSP 文案、file `<video>` error 提示 | Task 2 |
| 测试表 6 行 + test_web | Task 1–2 |
| openapi + README | Task 3 |
| 不录像、不改 CLI/事件 clip | 全局约束 |
