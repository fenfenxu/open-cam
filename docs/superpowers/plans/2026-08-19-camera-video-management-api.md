# 摄像头与视频管理 API Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-camera-video-management-api-design.md`。从 `origin/main` 开分支，不要把工作区里未提交的 Alembic/clip/doctor 改动当基线。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。

**Goal:** 把摄像头做成完整管理资源（PUT、级联删除、实时 health、手动重连、批量启停），并把上传视频做成独立 `/videos` 资源（列表/元数据/删除）。

**Architecture:** `/cameras` 管接入与生命周期；`/videos` 管 `data_dir/uploads` 文件库。摄像头仍用 `source_uri` 字符串，不引入 `video_id`。DB 的 `status` 只表示 running/stopped；health 从进程内 `CaptureWorker` 现读，不写库。`POST /cameras/upload` 保留为别名。

**Tech Stack:** FastAPI + SQLAlchemy SQLite（`create_all`）、Pydantic v2、httpx CLI、无构建原生 Web、pytest TestClient + mock detector。

## Global Constraints

- 规格原文：`docs/superpowers/specs/2026-08-19-camera-video-management-api-design.md`（错误文案、HTTP 码、字段名以规格为准）。
- 基线：`origin/main`。`init_db` 是 `Base.metadata.create_all` + 缺列 ALTER；新增 `videos` 表靠 ORM + `create_all`，**不要引入 Alembic**。
- Python ≥ 3.12；`from __future__ import annotations`；用户可见文案中文，标识符英文。
- CLI（`opencam/cli.py`）只能 import httpx/argparse 等轻量依赖，禁止 import 会加载 ultralytics/torch 的包内模块。
- 测试用 `tmp_settings`，`OPENCAM_DETECTOR=mock`，不下载模型、不依赖网络。
- 视频数据不出本机；不要把 api key 写入文件。
- 改 API 后必须 `uv run python scripts/export_openapi.py`。
- 命令：`uv run pytest`；单测示例 `uv run pytest tests/test_videos_api.py -v`。
- 验证合同：规格「验证方案」里的用例矩阵必须全部落地为 pytest，禁止只测 happy path。

## 范围边界

做：规格第 1–9 节全部。  
不做：`camera.video_id`、持久化 `CAMERA_ERROR`、WebSocket、无 ids 的全部启动、规则/检测/方案包改动、Web 批量按钮、本任务引入 Alembic。

## 验收标准（DoD）

与规格「验收」一致：

1. 摄像头可改名；运行中改源 409（detail 含 `请先停止摄像头再修改视频源`），停止后改源 200。
2. 删除有规则/事件的摄像头 204；规则/事件行消失；`snapshot_dir` 下该事件快照文件消失；`uploads/` 视频仍在。
3. 列表中 stopped 的 `health` 为 `null`；running 带 `health` 对象（含 `alive`/`has_frame`/`last_frame_age_sec`/`width`/`height`）。
4. `POST /cameras/batch/start` 与 `/stop` 返回逐路 `ok`；空 `ids` 为 422。
5. `/videos` 可上传/列出/删除；被摄像头 `source_uri` 引用时删除 409（`视频正被摄像头使用，无法删除`）。
6. `POST /cameras/upload` 仍 201 且 body 含 `path`。
7. 规格「验证方案」矩阵全部有自动化用例；`uv run pytest` 全绿；`docs/openapi.json` 含新路径。
8. 上传文件名路径穿越仍落在 `uploads/` 内；级联删除不碰 `snapshot_dir` 外的文件。
9. OpenCV 写出的小 mp4 上传后 `width`/`height`/`duration_sec` 为正；假字节为 null。

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | 视频库：模型 + `/videos` + upload 别名 | `tests/test_videos_api.py` 全绿；旧 upload 测试仍过 |
| 2 | 摄像头 PUT / 级联删除 / health | `tests/test_cameras_api.py` 中更新/删除/health 全绿 |
| 3 | 重连 + 批量启停 | 同文件 reconnect/batch 用例全绿 |
| 4 | CLI + Web + README/skill/openapi | CLI 冒烟 + `test_web.py` 静态资源 + openapi 含新路径 |

后一 stage 依赖前一 stage 已合入 `main`（或基于前一 PR 分支）。

## 文件地图

- Create: `opencam/api/videos.py`、`tests/test_videos_api.py`、`tests/test_cameras_api.py`、`tests/test_openapi_cameras.py`
- Modify: `opencam/models.py`、`opencam/api/cameras.py`、`opencam/main.py`、`opencam/cli.py`、`opencam/web/pages/cameras.js`、`tests/test_cli.py`、`tests/test_upload_api.py`（仅确认仍过）、`docs/openapi.json`、`README.md`、`skills/opencam/SKILL.md`、`AGENTS.md`（api/ 枚举加 videos）

---

### Task 1: 视频库模型与 `/videos` API

**Files:**
- Modify: `opencam/models.py`（在 Pydantic 区、`CameraOut` 之后追加 `Video` ORM + `VideoOut`）
- Create: `opencam/api/videos.py`
- Modify: `opencam/api/cameras.py`（`upload_video` 改为调用共享入库函数）
- Modify: `opencam/main.py`（import + `include_router` + `_TAGS` 增加 videos）
- Create: `tests/test_videos_api.py`
- Test: 现有 `tests/test_upload_api.py` 必须仍通过

**Interfaces:**
- Consumes: 现有 `POST /cameras/upload` 落盘规则（安全文件名、扩展名白名单、重名 `_n`、目录 `settings.data_dir / "uploads"`）
- Produces:
  - ORM `Video(id, filename, path, size_bytes, duration_sec, width, height, created_at)`
  - `VideoOut` 同上全部字段
  - `store_upload(file: UploadFile) -> Video`（落盘 + 探测 + 插行 + commit）
  - 路由：`POST/GET /videos`、`GET/DELETE /videos/{video_id}`
  - `POST /cameras/upload` 返回 `VideoOut`（JSON 必含 `path`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_videos_api.py`：

```python
"""视频库 API：上传入库、列表/详情、删除与引用保护；upload 别名兼容 path。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_upload_via_videos_and_list_get(client, tmp_settings):
    resp = client.post("/videos",
                       files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] >= 1
    assert body["filename"] == "demo.mp4"
    assert body["size_bytes"] == len(b"fake-video-bytes")
    assert body["path"].endswith("demo.mp4")
    saved = tmp_settings.data_dir / "uploads" / "demo.mp4"
    assert saved.read_bytes() == b"fake-video-bytes"
    # 假字节探测失败，元数据为 null
    assert body["duration_sec"] is None
    assert body["width"] is None
    assert body["height"] is None

    listed = client.get("/videos").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]

    got = client.get(f"/videos/{body['id']}")
    assert got.status_code == 200
    assert got.json()["path"] == body["path"]


def test_cameras_upload_alias_still_returns_path(client):
    resp = client.post("/cameras/upload",
                       files={"file": ("a.avi", b"first", "video/avi")})
    assert resp.status_code == 201
    assert "path" in resp.json()
    assert resp.json()["path"].endswith("a.avi")


def test_upload_rejects_unsupported_ext(client):
    resp = client.post("/videos",
                       files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 400
    assert "不支持的视频格式" in resp.json()["detail"]


def test_delete_unreferenced_video(client, tmp_settings):
    created = client.post("/videos",
                          files={"file": ("gone.mp4", b"x", "video/mp4")}).json()
    path = tmp_settings.data_dir / "uploads" / "gone.mp4"
    assert path.exists()
    resp = client.delete(f"/videos/{created['id']}")
    assert resp.status_code == 204
    assert not path.exists()
    assert client.get(f"/videos/{created['id']}").status_code == 404


def test_delete_referenced_video_conflict(client, tmp_settings):
    created = client.post("/videos",
                          files={"file": ("used.mp4", b"x", "video/mp4")}).json()
    cam = client.post("/cameras", json={
        "name": "c", "source_type": "file", "source_uri": created["path"],
    })
    assert cam.status_code == 201
    resp = client.delete(f"/videos/{created['id']}")
    assert resp.status_code == 409
    assert "视频正被摄像头使用，无法删除" in resp.json()["detail"]
    assert (tmp_settings.data_dir / "uploads" / "used.mp4").exists()


def test_video_not_found(client):
    assert client.get("/videos/999").status_code == 404
    assert "视频不存在" in client.get("/videos/999").json()["detail"]
    assert client.delete("/videos/999").status_code == 404


def test_videos_list_empty(client):
    assert client.get("/videos").json() == []


def test_upload_duplicate_name_not_overwritten(client, tmp_settings):
    r1 = client.post("/videos", files={"file": ("a.avi", b"first", "video/avi")})
    r2 = client.post("/videos", files={"file": ("a.avi", b"second", "video/avi")})
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["path"] != r2.json()["path"]
    uploads = tmp_settings.data_dir / "uploads"
    assert (uploads / "a.avi").read_bytes() == b"first"


def test_upload_sanitizes_path_traversal_filename(client, tmp_settings):
    from pathlib import Path

    resp = client.post("/videos",
                       files={"file": ("../../evil.mp4", b"x", "video/mp4")})
    assert resp.status_code == 201, resp.text
    saved = Path(resp.json()["path"]).resolve()
    root = (tmp_settings.data_dir / "uploads").resolve()
    assert saved == root or root in saved.parents
    assert ".." not in saved.name


def test_delete_video_after_camera_removed(client):
    created = client.post("/videos",
                          files={"file": ("later.mp4", b"x", "video/mp4")}).json()
    cam = client.post("/cameras", json={
        "name": "c", "source_type": "file", "source_uri": created["path"],
    })
    assert cam.status_code == 201
    assert client.delete(f"/cameras/{cam.json()['id']}").status_code == 204
    assert client.delete(f"/videos/{created['id']}").status_code == 204


def test_upload_probes_tiny_mp4_metadata(client, tmp_path):
    import cv2
    import numpy as np

    video = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (320, 240))
    for _ in range(20):
        writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
    writer.release()
    resp = client.post("/videos",
                       files={"file": ("tiny.mp4", video.read_bytes(), "video/mp4")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["width"] == 320
    assert body["height"] == 240
    assert body["duration_sec"] is not None
    assert body["duration_sec"] > 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_videos_api.py -v`  
Expected: FAIL（`/videos` 不存在或 404）

- [ ] **Step 3: 实现**

在 `opencam/models.py` 的 `Camera` 类之后、`Rule` 之前插入 ORM（表必须在 metadata 里才能 `create_all`）：

```python
class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(Text, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
```

在 `CameraOut` 之后追加：

```python
class VideoOut(BaseModel):
    id: int
    filename: str
    path: str
    size_bytes: int
    duration_sec: Optional[float]
    width: Optional[int]
    height: Optional[int]
    created_at: float

    model_config = {"from_attributes": True}
```

创建 `opencam/api/videos.py`。把现有 `cameras.py` 里的扩展名白名单、安全文件名、重名逻辑搬过来，探测用 OpenCV：

```python
"""视频库 API：上传文件落盘入库、列表/详情/删除。被摄像头 source_uri 引用时不可删。"""

from __future__ import annotations

import re
import time
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session, session_scope
from ..models import Camera, Video, VideoOut

router = APIRouter(prefix="/videos", tags=["videos"])

ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


def _safe_dest(filename: str | None) -> Path:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
    if ext not in ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"不支持的视频格式 {ext or '(无扩展名)'}，"
                                 f"支持: {', '.join(sorted(ALLOWED_VIDEO_EXTS))}")
    safe_name = re.sub(r"[^\w.()-]+", "_", filename or "video" + ext)
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / safe_name
    n = 1
    while dest.exists():
        dest = upload_dir / f"{dest.stem}_{n}{dest.suffix}"
        n += 1
    return dest


def _probe(path: Path) -> tuple[float | None, int | None, int | None]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, None, None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (frames / fps) if fps > 0 and frames > 0 else None
        return duration, (width or None), (height or None)
    finally:
        cap.release()


def store_upload(file: UploadFile) -> Video:
    dest = _safe_dest(file.filename)
    dest.write_bytes(file.file.read())
    duration, width, height = _probe(dest)
    session = get_session()
    try:
        video = Video(
            filename=dest.name,
            path=str(dest),
            size_bytes=dest.stat().st_size,
            duration_sec=duration,
            width=width,
            height=height,
            created_at=time.time(),
        )
        session.add(video)
        session.commit()
        session.refresh(video)
        return video
    finally:
        session.close()


@router.post("", response_model=VideoOut, status_code=201, summary="上传视频文件")
def upload_video(file: UploadFile):
    return store_upload(file)


@router.get("", response_model=list[VideoOut], summary="已上传视频列表")
def list_videos(session: Session = Depends(session_scope)):
    return session.query(Video).order_by(Video.id).all()


@router.get("/{video_id}", response_model=VideoOut, summary="视频详情")
def get_video(video_id: int, session: Session = Depends(session_scope)):
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    return video


@router.delete("/{video_id}", status_code=204, summary="删除已上传视频")
def delete_video(video_id: int, session: Session = Depends(session_scope)):
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    used = session.query(Camera).filter_by(source_uri=video.path).first()
    if used is not None:
        raise HTTPException(409, "视频正被摄像头使用，无法删除")
    path = Path(video.path)
    if path.exists():
        path.unlink()
    session.delete(video)
    session.commit()
    return Response(status_code=204)
```

`opencam/api/cameras.py` 的 `upload_video` 改为：

```python
from .videos import store_upload
from ..models import VideoOut

@router.post("/upload", response_model=VideoOut, status_code=201, summary="上传本地视频文件")
def upload_video(file: UploadFile):
    """别名：与 POST /videos 同一套入库，响应含 path 以兼容旧客户端。"""
    return store_upload(file)
```

删除 `cameras.py` 里不再使用的 `ALLOWED_VIDEO_EXTS`、`re` 落盘代码。保留摄像头其它路由。

`opencam/main.py`：

- `from .api import account, cameras, events, packs, rule_presets, rules, stats, system, videos`
- `_TAGS` 增加 `{"name": "videos", "description": "本机上传视频文件库（列表、元数据、删除）"}`
- `app.include_router(videos.router)`（建议放在 `cameras.router` 旁边）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_videos_api.py tests/test_upload_api.py -v`  
Expected: PASS（含重名、路径穿越、真 mp4 探测、删摄像头后再删视频）

- [ ] **Step 5: Commit / PR**

```bash
git add opencam/models.py opencam/api/videos.py opencam/api/cameras.py opencam/main.py tests/test_videos_api.py
git commit -m "$(cat <<'EOF'
feat: add /videos library API and keep cameras upload alias

EOF
)"
```

PR 标题含本 stage 的 issue key。

---

### Task 2: 摄像头 PUT、health、级联删除

**Files:**
- Modify: `opencam/models.py`（`CameraUpdate`、`CameraHealth`、`CameraOut.health`）
- Modify: `opencam/api/cameras.py`（`camera_out` 填充 health；PUT；DELETE 级联）
- Create: `tests/test_cameras_api.py`（本 task 先写入更新/删除/health 用例；reconnect/batch 在 Task 3 追加）

**Interfaces:**
- Consumes: Task 1 的 `Video` / 上传目录；`camera_manager.get` / `latest_frame` / `is_alive`；`CaptureWorker.last_frame_at`（初值 0 表示从未出帧）
- Produces:
  - `CameraUpdate`：可选 `name` / `source_type` / `source_uri`，全空则校验失败 → 422
  - `CameraHealth(alive: bool, has_frame: bool, last_frame_age_sec: Optional[float], width: Optional[int], height: Optional[int])`
  - `CameraOut.health: Optional[CameraHealth] = None`
  - `camera_out(camera: Camera) -> CameraOut`：stopped 或无 worker 时 `health=None`
  - `PUT /cameras/{camera_id}`
  - DELETE：先 stop（若 running）→ 删 snapshot_dir 内快照文件 → 删 Event → 删 Rule → 删 Camera；不删 uploads

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cameras_api.py`：

```python
"""摄像头管理 API：更新 409 语义、级联删除、health 形状。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import CAMERA_RUNNING, Camera, Event, Rule


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _make_camera(client, **kw) -> dict:
    body = {"name": "测试摄像头", "source_type": "file",
            "source_uri": "/tmp/nonexistent.mp4", **kw}
    resp = client.post("/cameras", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_stopped_camera_health_is_null(client):
    cam = _make_camera(client)
    got = client.get(f"/cameras/{cam['id']}").json()
    assert got["health"] is None
    listed = client.get("/cameras").json()
    assert listed[0]["health"] is None


def test_put_rename_while_running(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        row = session.get(Camera, cam["id"])
        row.status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    resp = client.put(f"/cameras/{cam['id']}", json={"name": "新名称"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "新名称"


def test_put_source_while_running_conflict(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        row = session.get(Camera, cam["id"])
        row.status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    resp = client.put(f"/cameras/{cam['id']}", json={"source_uri": "/tmp/other.mp4"})
    assert resp.status_code == 409
    assert "请先停止摄像头再修改视频源" in resp.json()["detail"]
    assert client.get(f"/cameras/{cam['id']}").json()["source_uri"] == cam["source_uri"]


def test_put_source_after_stop(client):
    cam = _make_camera(client)
    resp = client.put(f"/cameras/{cam['id']}", json={"source_uri": "/tmp/other.mp4"})
    assert resp.status_code == 200
    assert resp.json()["source_uri"] == "/tmp/other.mp4"


def test_put_empty_body_unprocessable(client):
    cam = _make_camera(client)
    resp = client.put(f"/cameras/{cam['id']}", json={})
    assert resp.status_code == 422


def test_camera_not_found(client):
    assert client.get("/cameras/999").status_code == 404
    assert client.put("/cameras/999", json={"name": "x"}).status_code == 404
    assert client.delete("/cameras/999").status_code == 404


def test_delete_cascades_rules_events_snapshots_keeps_uploads(client, tmp_settings):
    from opencam.config import settings

    video = client.post("/cameras/upload",
                        files={"file": ("keep.mp4", b"abc", "video/mp4")}).json()
    cam = _make_camera(client, source_uri=video["path"])
    camera_id = cam["id"]

    session = get_session()
    try:
        rule = Rule(camera_id=camera_id, name="入侵", type="zone_intrusion",
                    params={}, enabled=True, cooldown=5)
        session.add(rule)
        session.commit()
        snap = settings.snapshot_dir
        snap.mkdir(parents=True, exist_ok=True)
        snap_file = snap / f"cam{camera_id}_test.jpg"
        snap_file.write_bytes(b"jpeg")
        event = Event(camera_id=camera_id, rule_id=rule.id, type="zone_intrusion",
                      confidence=0.9, snapshot_path=str(snap_file), detail={})
        session.add(event)
        session.commit()
    finally:
        session.close()

    resp = client.delete(f"/cameras/{camera_id}")
    assert resp.status_code == 204
    session = get_session()
    try:
        assert session.query(Rule).filter_by(camera_id=camera_id).count() == 0
        assert session.query(Event).filter_by(camera_id=camera_id).count() == 0
        assert session.get(Camera, camera_id) is None
    finally:
        session.close()
    assert not snap_file.exists()
    assert Path(video["path"]).exists()


def test_put_source_type_while_running_conflict(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        session.get(Camera, cam["id"]).status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    resp = client.put(f"/cameras/{cam['id']}", json={"source_type": "rtsp"})
    assert resp.status_code == 409
    assert "请先停止摄像头再修改视频源" in resp.json()["detail"]


def test_put_invalid_source_type(client):
    cam = _make_camera(client)
    resp = client.put(f"/cameras/{cam['id']}", json={"source_type": "ftp"})
    assert resp.status_code == 422


def test_delete_camera_without_children(client):
    cam = _make_camera(client)
    assert client.delete(f"/cameras/{cam['id']}").status_code == 204
    assert client.get(f"/cameras/{cam['id']}").status_code == 404


def test_delete_does_not_remove_snapshot_outside_dir(client, tmp_path):
    from opencam.config import settings

    cam = _make_camera(client)
    outsider = tmp_path / "outside.jpg"
    outsider.write_bytes(b"keep-me")
    session = get_session()
    try:
        event = Event(camera_id=cam["id"], type="zone_intrusion",
                      confidence=0.9, snapshot_path=str(outsider), detail={})
        session.add(event)
        session.commit()
    finally:
        session.close()
    assert client.delete(f"/cameras/{cam['id']}").status_code == 204
    assert outsider.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cameras_api.py -v`  
Expected: FAIL（PUT 405 或 health 字段缺失）

- [ ] **Step 3: 实现**

`opencam/models.py`：`from pydantic import BaseModel, Field, model_validator`。替换 `CameraOut` 并新增：

```python
class CameraUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = Field(default=None, pattern="^(file|rtsp)$")
    source_uri: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.name is None and self.source_type is None and self.source_uri is None:
            raise ValueError("至少提供一个字段")
        return self


class CameraHealth(BaseModel):
    alive: bool
    has_frame: bool
    last_frame_age_sec: Optional[float]
    width: Optional[int]
    height: Optional[int]


class CameraOut(BaseModel):
    id: int
    name: str
    source_type: str
    source_uri: str
    status: str
    health: Optional[CameraHealth] = None

    model_config = {"from_attributes": True}
```

`opencam/api/cameras.py`：增加 `import time`、`from pathlib import Path`。用 `CameraUpdate`、`CameraHealth`、`CameraOut`。所有返回摄像头的处理器改为 `return camera_out(camera)`，不要直接返回 ORM（否则 `health` 不会被填充）。

```python
def _health_for(camera_id: int, status: str) -> CameraHealth | None:
    if status != CAMERA_RUNNING:
        return None
    worker = camera_manager.get(camera_id)
    if worker is None:
        return None
    frame = worker.latest_frame()
    has_frame = frame is not None
    age = None
    if worker.last_frame_at:  # 0.0 视为从未出帧
        age = time.monotonic() - worker.last_frame_at
    width = height = None
    if frame is not None:
        height, width = int(frame.shape[0]), int(frame.shape[1])
    return CameraHealth(
        alive=worker.is_alive(),
        has_frame=has_frame,
        last_frame_age_sec=age,
        width=width,
        height=height,
    )


def camera_out(camera: Camera) -> CameraOut:
    return CameraOut(
        id=camera.id,
        name=camera.name,
        source_type=camera.source_type,
        source_uri=camera.source_uri,
        status=camera.status,
        health=_health_for(camera.id, camera.status),
    )
```

`list_cameras` / `create_camera` / `get_camera` / `start` / `stop` 的 return 全部包一层 `camera_out`。list 用 `[camera_out(c) for c in ...]`。

PUT（声明在 `GET /{camera_id}` 同路径；FastAPI 允许多方法）：

```python
@router.put("/{camera_id}", response_model=CameraOut, summary="更新摄像头")
def update_camera(camera_id: int, body: CameraUpdate,
                  session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    source_touched = "source_type" in body.model_fields_set or "source_uri" in body.model_fields_set
    if source_touched and camera.status == CAMERA_RUNNING:
        raise HTTPException(409, "请先停止摄像头再修改视频源")
    if body.name is not None:
        camera.name = body.name
    if body.source_type is not None:
        camera.source_type = body.source_type
    if body.source_uri is not None:
        camera.source_uri = body.source_uri
    session.commit()
    session.refresh(camera)
    return camera_out(camera)
```

替换 `delete_camera` 主体（404 / running 先 stop 保持不变）：

```python
    from ..config import settings
    from ..models import Event, Rule

    events = session.query(Event).filter_by(camera_id=camera_id).all()
    snap_root = settings.snapshot_dir.resolve()
    for event in events:
        if not event.snapshot_path:
            continue
        path = Path(event.snapshot_path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.exists() and snap_root in resolved.parents or resolved.parent == snap_root:
            # 仅删除 snapshot_dir 树内文件
            if snap_root == resolved or snap_root in resolved.parents:
                resolved.unlink(missing_ok=True)
    session.query(Event).filter_by(camera_id=camera_id).delete()
    session.query(Rule).filter_by(camera_id=camera_id).delete()
    session.delete(camera)
    session.commit()
    return Response(status_code=204)
```

路径安全写成一次清晰判断，避免运算符优先级错误。正确形式：

```python
        if resolved.exists() and (resolved == snap_root or snap_root in resolved.parents):
            resolved.unlink(missing_ok=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_cameras_api.py tests/test_cli.py tests/test_events_api.py -v`  
Expected: PASS（CLI/事件测试会拿到多出来的 `health: null`，断言未写死全字段则应仍过）

- [ ] **Step 5: Commit / PR**

```bash
git commit -m "$(cat <<'EOF'
feat: add camera PUT, live health, and cascading delete

EOF
)"
```

---

### Task 3: 手动重连与批量启停

**Files:**
- Modify: `opencam/api/cameras.py`（在 `/{camera_id}/start` **之前** 注册 batch 路由）
- Modify: `opencam/models.py`（可在文件底部增加 `BatchIds` / `BatchResultItem` / `BatchResult`，或放在 cameras.py 内；计划要求放 `models.py` 以便 OpenAPI 稳定）
- Modify: `tests/test_cameras_api.py`（追加用例）

**Interfaces:**
- Consumes: 现有 `start_camera` / `stop_camera`；Task 2 的 `camera_out`
- Produces:
  - `BatchIds(ids: list[int])`，`min_length=1`
  - `BatchResultItem(id: int, ok: bool, error: Optional[str] = None)`
  - `BatchResult(results: list[BatchResultItem])`
  - `POST /cameras/batch/start`、`POST /cameras/batch/stop` → 200 `BatchResult`（单路失败不抬成 500）
  - `POST /cameras/{camera_id}/reconnect`：stopped → 409 `仅运行中的摄像头可以重连`；running → `stop_camera` + `start_camera`（必须真重启，不能走幂等 start）

- [ ] **Step 1: 追加失败测试到 `tests/test_cameras_api.py`**

```python
def test_reconnect_stopped_conflict(client):
    cam = _make_camera(client)
    resp = client.post(f"/cameras/{cam['id']}/reconnect")
    assert resp.status_code == 409
    assert "仅运行中的摄像头可以重连" in resp.json()["detail"]


def test_reconnect_running_ok(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        session.get(Camera, cam["id"]).status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    # 无真实采集线程时 start_camera 仍会把 status 设回 running（文件打不开也不抛）
    resp = client.post(f"/cameras/{cam['id']}/reconnect")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"


def test_batch_start_partial(client):
    cam = _make_camera(client)
    resp = client.post("/cameras/batch/start", json={"ids": [cam["id"], 999]})
    assert resp.status_code == 200, resp.text
    results = {item["id"]: item for item in resp.json()["results"]}
    assert results[cam["id"]]["ok"] is True
    assert results[999]["ok"] is False
    assert "摄像头不存在" in results[999]["error"]


def test_batch_stop_idempotent(client):
    cam = _make_camera(client)
    resp = client.post("/cameras/batch/stop", json={"ids": [cam["id"]]})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["ok"] is True


def test_batch_empty_ids_unprocessable(client):
    resp = client.post("/cameras/batch/start", json={"ids": []})
    assert resp.status_code == 422


def test_batch_missing_ids_unprocessable(client):
    assert client.post("/cameras/batch/start", json={}).status_code == 422
    assert client.post("/cameras/batch/stop", json={}).status_code == 422


def test_reconnect_not_found(client):
    resp = client.post("/cameras/999/reconnect")
    assert resp.status_code == 404
    assert "摄像头不存在" in resp.json()["detail"]


def test_batch_results_preserve_id_order(client):
    cam = _make_camera(client)
    resp = client.post("/cameras/batch/stop", json={"ids": [999, cam["id"]]})
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["results"]]
    assert ids == [999, cam["id"]]
```

说明：`test_reconnect_running_ok` 若 `start_camera` 因文件不存在抛错，则断言 500 且 detail 以 `重连失败:` 开头亦可；实现时应与现网 `start_camera` 行为一致——打开失败通常只打日志、DB 仍标 running。以实际 `start_camera` 为准：不抛则 200。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cameras_api.py::test_batch_start_partial tests/test_cameras_api.py::test_reconnect_stopped_conflict -v`  
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现**

`opencam/models.py`：

```python
class BatchIds(BaseModel):
    ids: list[int] = Field(min_length=1)


class BatchResultItem(BaseModel):
    id: int
    ok: bool
    error: Optional[str] = None


class BatchResult(BaseModel):
    results: list[BatchResultItem]
```

`opencam/api/cameras.py` 在 `get_camera` **之前**插入 batch（静态段优先）：

```python
@router.post("/batch/start", response_model=BatchResult, summary="批量启动摄像头")
def batch_start(body: BatchIds, session: Session = Depends(session_scope)):
    return _batch(body.ids, start=True, session=session)


@router.post("/batch/stop", response_model=BatchResult, summary="批量停止摄像头")
def batch_stop(body: BatchIds, session: Session = Depends(session_scope)):
    return _batch(body.ids, start=False, session=session)
```

`_batch`：对每个 id `session.get(Camera, id)`；没有则 `ok=False, error="摄像头不存在"`。有则调用与单路相同的 start/stop 逻辑（已 running 的 start 幂等成功；已 stopped 的 stop 幂等成功）。`start_camera` 抛错则该路 `ok=False, error=str(exc)`，继续下一个。返回 `BatchResult(results=...)`。`ok=True` 的 item 不要设 `error`（保持 null/省略，Pydantic 默认 None）。

reconnect 放在 start/stop 附近：

```python
@router.post("/{camera_id}/reconnect", response_model=CameraOut, summary="重连摄像头")
def reconnect(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status != CAMERA_RUNNING:
        raise HTTPException(409, "仅运行中的摄像头可以重连")
    try:
        stop_camera(camera_id)
        start_camera(camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"重连失败: {exc}") from exc
    session.refresh(camera)
    return camera_out(camera)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_cameras_api.py -v`  
Expected: PASS

- [ ] **Step 5: Commit / PR**

```bash
git commit -m "$(cat <<'EOF'
feat: add camera reconnect and batch start/stop

EOF
)"
```

---

### Task 4: CLI、Web 控制台、文档与 OpenAPI

**Files:**
- Modify: `opencam/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `opencam/web/pages/cameras.js`
- Modify: `tests/test_web.py`（若 cameras.js 新增文案可断言「已上传」或 ` /videos`）
- Modify: `README.md` API 表格、`skills/opencam/SKILL.md`、`AGENTS.md` 的 `api/` 枚举
- Run: `uv run python scripts/export_openapi.py` → `docs/openapi.json`

**Interfaces:**
- Consumes: Task 1–3 的全部 HTTP 路径
- Produces: CLI 子命令与 Web 行为见规格「CLI」「Web 控制台」；OpenAPI 含 `/videos`、`PUT /cameras/{camera_id}`、`/cameras/batch/start`、`/cameras/{camera_id}/reconnect`

- [ ] **Step 1: CLI 失败测试**

在 `tests/test_cli.py` 追加（沿用现有 `cli_env` / `run_cli`）：

```python
def test_cameras_update(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    updated = run_cli(capsys, "cameras", "update", "1", "--name", "后门")
    assert updated["name"] == "后门"


def test_videos_list_after_upload(cli_env, capsys, tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"fake")
    uploaded = run_cli(capsys, "videos", "upload", str(video))
    assert uploaded["path"].endswith("c.mp4")
    listed = run_cli(capsys, "videos", "list")
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]
    got = run_cli(capsys, "videos", "get", str(uploaded["id"]))
    assert got["id"] == uploaded["id"]
    cli.main(["videos", "delete", str(uploaded["id"])])
    capsys.readouterr()
    assert run_cli(capsys, "videos", "list") == []


def test_cameras_update_requires_a_field(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cameras", "update", "1"])
    assert exc.value.code == 1
    assert "至少指定" in capsys.readouterr().err


def test_cameras_reconnect_stopped_exits(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cameras", "reconnect", "1"])
    assert exc.value.code == 1
    assert "仅运行中的摄像头可以重连" in capsys.readouterr().err


def test_cameras_batch_start_partial(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    body = run_cli(capsys, "cameras", "batch-start", "1", "999")
    results = {item["id"]: item for item in body["results"]}
    assert results[1]["ok"] is True
    assert results[999]["ok"] is False
```

- [ ] **Step 2: 跑 CLI 测试确认失败**

Run: `uv run pytest tests/test_cli.py::test_cameras_update tests/test_cli.py::test_videos_list_after_upload -v`  
Expected: FAIL（无 `update` / `videos` 子命令）

- [ ] **Step 3: 实现 CLI**

扩展 `_request`，支持 multipart（json 与 files 互斥）：

```python
def _request(client: httpx.Client, method: str, path: str,
             params: Optional[dict] = None, body: Any = None,
             raw: bool = False, files: Any = None) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        kwargs: dict[str, Any] = {"params": params}
        if files is not None:
            kwargs["files"] = files
        elif body is not None:
            kwargs["json"] = body
        resp = client.request(method, path, **kwargs)
```

`_cameras` 增加：

```python
    elif args.action == "update":
        payload = {}
        if args.name is not None:
            payload["name"] = args.name
        if args.source_type is not None:
            payload["source_type"] = args.source_type
        if args.source_uri is not None:
            payload["source_uri"] = args.source_uri
        if not payload:
            raise CliError("请至少指定 --name / --source-type / --source-uri 之一")
        _emit(_request(client, "PUT", f"/cameras/{args.id}", body=payload),
              args.pretty)
    elif args.action == "reconnect":
        _emit(_request(client, "POST", f"/cameras/{args.id}/reconnect"),
              args.pretty)
    elif args.action == "batch-start":
        _emit(_request(client, "POST", "/cameras/batch/start",
                       body={"ids": args.ids}), args.pretty)
    elif args.action == "batch-stop":
        _emit(_request(client, "POST", "/cameras/batch/stop",
                       body={"ids": args.ids}), args.pretty)
```

新增 `_videos`：

```python
def _videos(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/videos"), args.pretty)
    elif args.action == "get":
        _emit(_request(client, "GET", f"/videos/{args.id}"), args.pretty)
    elif args.action == "upload":
        with open(args.path, "rb") as fh:
            files = {"file": (os.path.basename(args.path), fh)}
            _emit(_request(client, "POST", "/videos", files=files), args.pretty)
    elif args.action == "delete":
        _request(client, "DELETE", f"/videos/{args.id}")
        print(f"视频 {args.id} 已删除")
```

`build_parser` 中 cameras 子解析器追加（在 snapshot 旁）：

```python
    q = sp.add_parser("update", help="更新摄像头")
    q.add_argument("id", type=int)
    q.add_argument("--name")
    q.add_argument("--source-type", choices=["file", "rtsp"])
    q.add_argument("--source-uri")
    q = sp.add_parser("reconnect", help="重连运行中的摄像头")
    q.add_argument("id", type=int)
    q = sp.add_parser("batch-start", help="批量启动")
    q.add_argument("ids", nargs="+", type=int)
    q = sp.add_parser("batch-stop", help="批量停止")
    q.add_argument("ids", nargs="+", type=int)
```

以及 videos 资源（与 cameras 并列）：

```python
    p = sub.add_parser("videos", help="上传视频库")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="已上传视频列表")
    q = sp.add_parser("get", help="视频详情"); q.add_argument("id", type=int)
    q = sp.add_parser("upload", help="上传本地文件")
    q.add_argument("path")
    q = sp.add_parser("delete", help="删除上传文件"); q.add_argument("id", type=int)
    p.set_defaults(func=_videos)
```

- [ ] **Step 4: Web 摄像头页**

改 `opencam/web/pages/cameras.js`：

1. 表格增加「保存」：名称、源类型、源地址做成 input（可用 `data-id` + 类名），按钮 `data-act="save"`。点击后 `PUT /cameras/${id}`，body 为 `{name, source_type, source_uri}`。运行中改源会 409，`api()` 已把 `detail` 丢进 Error.message，现有 `toast(err.message, true)` 即可。
2. 页面增加「已上传视频」区块：`GET /videos` 渲染表格（filename、size_bytes、duration_sec/width/height 为 null 时显示 `—`），删除按钮 `DELETE /videos/${id}`。
3. 文件上传成功后：`uriInput.value = body.path` 并调用视频列表刷新。

reload 拆成 `reloadCameras()` + `reloadVideos()`。不要加批量启停按钮。

`tests/test_web.py` 的 `test_static_assets` 已覆盖 `cameras.js` 可下载。追加：

```python
def test_cameras_page_has_video_library(client):
    js = client.get("/static/pages/cameras.js").text
    assert "/videos" in js
    assert "data-act=\"save\"" in js or "data-act='save'" in js
    assert "method: 'PUT'" in js or 'method: "PUT"' in js or "method: `PUT`" in js
```

创建 `tests/test_openapi_cameras.py`：

```python
"""OpenAPI 契约：新管理路径必须出现在运行时 schema。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_openapi_includes_camera_video_management_paths(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/videos" in paths
    assert "get" in paths["/videos"] and "post" in paths["/videos"]
    assert "/videos/{video_id}" in paths
    assert "put" in paths["/cameras/{camera_id}"]
    assert "/cameras/batch/start" in paths
    assert "/cameras/batch/stop" in paths
    assert "/cameras/{camera_id}/reconnect" in paths
```

- [ ] **Step 5: 文档与 OpenAPI**

`README.md` 表格（摄像头那几行）改为包含：

- `PUT /cameras/{id}` 更新（运行中改源 409）
- `POST /cameras/{id}/reconnect`
- `POST /cameras/batch/start`、`/batch/stop`
- `GET/POST /videos`、`GET/DELETE /videos/{id}`
- `POST /cameras/upload` 注明与 `/videos` 同一套入库

`skills/opencam/SKILL.md`「摄像头与规则管理」增加：

```bash
opencam cameras update 1 --name 后门
opencam cameras reconnect 1
opencam cameras batch-start 1 2
opencam videos list
opencam videos upload /v/demo.mp4
```

`AGENTS.md` 代码组织里 `api/` 枚举加上 `videos`。

```bash
uv run python scripts/export_openapi.py
uv run pytest tests/test_openapi_cameras.py tests/test_cli.py tests/test_web.py tests/test_videos_api.py tests/test_cameras_api.py tests/test_upload_api.py -v
uv run pytest
```

Expected: 全绿；`docs/openapi.json` 的 `paths` 含 `/videos`、`/cameras/{camera_id}` 的 `put`、`/cameras/batch/start`、`/cameras/{camera_id}/reconnect`。规格「验证方案」矩阵无空行。

- [ ] **Step 6: Commit / PR**

```bash
git commit -m "$(cat <<'EOF'
feat: expose camera/video management in CLI, web, and OpenAPI

EOF
)"
```

---

## Spec coverage（自检）

| 规格项 | 任务 |
|---|---|
| PUT + 运行中改源 409 + 改名允许 | Task 2 |
| 级联删除事件/规则/快照、保留 uploads | Task 2 |
| 快照目录外文件不删 | Task 2 |
| health 字段与 last_frame_at==0 | Task 2 |
| reconnect + 404 | Task 3 |
| batch start/stop、空/缺 ids 422、顺序保持 | Task 3 |
| /videos CRUD + 引用 409 + 重名 + 路径穿越 | Task 1 |
| 真 mp4 元数据探测 | Task 1 |
| /cameras/upload 别名含 path | Task 1 |
| CLI 更新/上传/重连失败/批量部分失败 | Task 4 |
| Web 改名/改源 + 视频列表 | Task 4 |
| OpenAPI 运行时路径断言 + 快照 | Task 4 |
| 验证方案矩阵 | 规格「验证方案」；各 Task 测试块 |
| 不引入 Alembic / video_id / CAMERA_ERROR / Web 批量按钮 | 全局约束 |
