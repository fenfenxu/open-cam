"""Web 控制台冒烟：构建产物可访问，History 路由回退 HTML。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _require_dist():
    if not (DIST / "index.html").is_file():
        pytest.skip("web/dist 未构建，先运行 make web-build")


def _built_js() -> str:
    _require_dist()
    chunks: list[str] = []
    for path in DIST.rglob("*.js"):
        chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def test_console_index(client):
    _require_dist()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text
    assert "/static/app.js" not in resp.text


def test_events_history_fallback(client):
    _require_dist()
    resp = client.get("/events", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text


def test_training_wizard_page_wired(client):
    _require_dist()
    html = client.get("/").text
    assert "open-cam" in html
    js = _built_js()
    assert "/training/tasks" in js
    assert "说需求" in js
    assert "/models" in js
    assert "/events/" in js
    assert "false_alarm" in js
    assert "miss" in js
    assert "/feedback" in js


def test_events_page_shows_camera_and_clip(client):
    js = _built_js()
    assert "/clip" in js
    assert "source_offset" in js
    assert "camera_name" in js
    assert "source_filename" in js
    assert "素材" in js


def test_cameras_page_has_video_library(client):
    js = _built_js()
    assert "/videos" in js
    assert "data-act" in js and "save" in js
    assert "PUT" in js
    assert "请新建" in js


def test_camera_detail_live_and_replay_copy(client):
    js = _built_js()
    assert "/live.mjpg" in js
    assert "/source" in js
    assert "该源为直播流，不支持回放" in js
    assert "/cameras/" in js
