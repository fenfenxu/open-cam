"""Web 控制台冒烟：构建产物存在，History 路由刷新回 HTML。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DIST_INDEX = Path(__file__).resolve().parents[1] / "web" / "dist" / "index.html"

pytestmark = pytest.mark.skipif(
    not DIST_INDEX.is_file(),
    reason="web/dist 未构建，先运行 make web-build",
)


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
    assert "/static/app.js" not in resp.text
    assert "/assets/" in resp.text


def test_legacy_native_assets_gone(client):
    html = client.get("/").text
    assert 'id="root"' in html
    assert "/static/app.js" not in html
    resp = client.get("/static/pages/events.js")
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text


def test_spa_fallback_events_html(client):
    resp = client.get("/events", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text


def test_spa_fallback_unmatched_route(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text


def test_events_api_still_json(client):
    resp = client.get("/events")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert isinstance(resp.json(), list)


def test_spa_does_not_escape_dist(client):
    resp = client.get("/../opencam/main.py", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "def health" not in resp.text
    assert "open-cam" in resp.text
