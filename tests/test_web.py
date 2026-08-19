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
                 "/static/pages/marketplace.js", "/static/pages/settings.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_cameras_page_has_video_library(client):
    js = client.get("/static/pages/cameras.js").text
    assert "/videos" in js
    assert "data-act=\"save\"" in js or "data-act='save'" in js
