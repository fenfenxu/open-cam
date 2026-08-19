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
