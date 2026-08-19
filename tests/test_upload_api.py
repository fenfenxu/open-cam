"""视频上传 API：格式校验 + 落盘路径返回 + 重名不覆盖。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_upload_video(client, tmp_settings):
    resp = client.post("/cameras/upload",
                       files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")})
    assert resp.status_code == 201, resp.text
    path = resp.json()["path"]
    assert path.endswith("demo.mp4")
    saved = tmp_settings.data_dir / "uploads" / "demo.mp4"
    assert saved.read_bytes() == b"fake-video-bytes"


def test_upload_rejects_unsupported_ext(client):
    resp = client.post("/cameras/upload",
                       files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 400
    assert "不支持的视频格式" in resp.json()["detail"]


def test_upload_duplicate_name_not_overwritten(client, tmp_settings):
    r1 = client.post("/cameras/upload",
                     files={"file": ("a.avi", b"first", "video/avi")})
    r2 = client.post("/cameras/upload",
                     files={"file": ("a.avi", b"second", "video/avi")})
    assert r1.status_code == 201 and r2.status_code == 201
    p1, p2 = r1.json()["path"], r2.json()["path"]
    assert p1 != p2
    uploads = tmp_settings.data_dir / "uploads"
    assert (uploads / "a.avi").read_bytes() == b"first"
