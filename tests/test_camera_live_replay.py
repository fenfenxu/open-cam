"""摄像头直播 MJPEG 与文件源回放。"""

from __future__ import annotations

import threading
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
    assert client.get("/api/cameras/999/live.mjpg").status_code == 404
    assert "摄像头不存在" in client.get("/api/cameras/999/live.mjpg").json()["detail"]
    assert client.get("/api/cameras/999/source").status_code == 404
    assert "摄像头不存在" in client.get("/api/cameras/999/source").json()["detail"]


def test_stopped_file_camera_live_is_503(client):
    cid = client.post("/api/cameras", json={
        "name": "停", "source_type": "file", "source_uri": "/tmp/x.mp4",
    }).json()["id"]
    resp = client.get(f"/api/cameras/{cid}/live.mjpg")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "暂无可用帧（摄像头未运行或流未就绪）"


def test_rtsp_source_rejected(client):
    cid = client.post("/api/cameras", json={
        "name": "流", "source_type": "rtsp",
        "source_uri": "rtsp://127.0.0.1:8554/test",
    }).json()["id"]
    resp = client.get(f"/api/cameras/{cid}/source")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "该源为直播流，不支持文件回放"


def test_file_source_serves_mp4(client, tmp_path):
    video = tmp_path / "scene.mp4"
    _write_tiny_mp4(video)
    cid = client.post("/api/cameras", json={
        "name": "文件", "source_type": "file", "source_uri": str(video),
    }).json()["id"]
    resp = client.get(f"/api/cameras/{cid}/source")
    assert resp.status_code == 200, resp.text
    assert "video" in resp.headers["content-type"]
    assert len(resp.content) > 100


def test_missing_file_source_404(client):
    cid = client.post("/api/cameras", json={
        "name": "缺", "source_type": "file",
        "source_uri": "/tmp/opencam-no-such.mp4",
    }).json()["id"]
    resp = client.get(f"/api/cameras/{cid}/source")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "源文件不存在"


def test_running_camera_live_mjpeg_has_jpeg(client, tmp_path):
    video = tmp_path / "live.mp4"
    _write_tiny_mp4(video, frames=60, fps=10)
    cid = client.post("/api/cameras", json={
        "name": "播", "source_type": "file", "source_uri": str(video),
        "autostart": True,
    }).json()["id"]
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = client.get(f"/api/cameras/{cid}/snapshot.jpg")
            if snap.status_code == 200:
                break
            time.sleep(0.2)
        else:
            pytest.fail("启动后 5s 内没有快照帧")
        # httpx ASGITransport 会收齐整个 body 才返回；无限 MJPEG 会卡住。
        # 稍后停采集，让生成器退出，流里仍应已有 JPEG SOI。
        def _stop_after_burst() -> None:
            time.sleep(0.5)
            from opencam.pipeline import stop_camera
            stop_camera(cid)

        threading.Thread(target=_stop_after_burst, daemon=True).start()
        with client.stream("GET", f"/api/cameras/{cid}/live.mjpg") as resp:
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
        client.post(f"/api/cameras/{cid}/stop")
