"""Web 控制台冒烟：首页与静态资源可访问。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_console_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text
    assert "/static/app.js" in resp.text


def test_static_assets(client):
    for path in ("/static/style.css", "/static/app.js",
                 "/static/pages/dashboard.js", "/static/pages/rules.js",
                 "/static/pages/events.js", "/static/pages/cameras.js",
                 "/static/pages/camera.js",
                 "/static/pages/marketplace.js", "/static/pages/settings.js",
                 "/static/pages/training.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_training_wizard_page_wired(client):
    html = client.get("/").text
    assert 'href="#/training"' in html
    assert 'data-route="training"' in html
    app = client.get("/static/app.js").text
    assert "pages/training.js" in app
    js = client.get("/static/pages/training.js").text
    assert "/training/tasks" in js
    assert "说需求" in js
    assert "/models" in js
    events = client.get("/static/pages/events.js").text
    assert "/events/" in events
    assert "false_alarm" in events
    assert "miss" in events
    assert "/feedback" in events


def test_events_page_shows_camera_and_clip(client):
    js = client.get("/static/pages/events.js").text
    assert "/clip" in js
    assert "source_offset" in js
    assert "camera_name" in js
    assert "source_filename" in js
    assert "素材" in js
    assert "fmtClipRange" in js
    assert "fmtClipRange" not in client.get("/static/app.js").text


def test_events_page_defaults_to_todos(client):
    js = client.get("/static/pages/events.js").text
    assert "needs_action" in js
    assert "待办" in js


def test_cameras_page_has_video_library(client):
    js = client.get("/static/pages/cameras.js").text
    assert "/videos" in js
    assert "data-act=\"save\"" in js or "data-act='save'" in js
    assert "method: 'PUT'" in js or 'method: "PUT"' in js or "method: `PUT`" in js
    # 已创建摄像头：名称可改，类型/源地址只读
    assert "class=\"c-name\"" in js
    assert "class=\"c-type\"" not in js
    assert "class=\"c-uri\"" not in js
    assert "source_type: row.querySelector" not in js
    assert "source_uri: row.querySelector" not in js
    assert "请新建" in js


def test_camera_detail_live_and_replay_copy(client):
    js = client.get("/static/pages/camera.js").text
    assert "/live.mjpg" in js
    assert "/source" in js
    assert "该源为直播流，不支持回放" in js
    app = client.get("/static/app.js").text
    assert "cameras/" in app
    dash = client.get("/static/pages/dashboard.js").text
    assert "#/cameras/" in dash
