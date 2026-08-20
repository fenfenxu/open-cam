"""Web 控制台冒烟：构建产物存在，History 路由刷新回 HTML。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DIST_INDEX = Path(__file__).resolve().parents[1] / "web" / "out" / "index.html"

pytestmark = pytest.mark.skipif(
    not DIST_INDEX.is_file(),
    reason="web/out 未构建，先运行 make ui-build",
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
    assert "/_next/" in resp.text


def test_legacy_native_assets_gone(client):
    html = client.get("/").text
    assert "open-cam" in html
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


def test_cameras_fetch_not_html_when_sec_fetch_dest_empty(client):
    """浏览器 fetch 的 Sec-Fetch-Dest 是 empty；即使 Accept 带 text/html 也必须走 REST。
    否则 200 HTML 会被当成 JSON 解析，报 Unexpected token '<'。"""
    resp = client.get(
        "/cameras",
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Sec-Fetch-Dest": "empty",
        },
    )
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert isinstance(resp.json(), list)


def test_cameras_document_navigation_still_html(client):
    resp = client.get(
        "/cameras",
        headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "open-cam" in resp.text


def test_cameras_trailing_slash_fetch_is_json(client):
    """Next trailingSlash 会访问 /cameras/；目录 index.html 不能抢走 REST。"""
    resp = client.get("/cameras/", headers={"Accept": "*/*"})
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert isinstance(resp.json(), list)


def test_spa_html_not_cached(client):
    resp = client.get("/cameras", headers={"Accept": "text/html"})
    assert "no-store" in resp.headers.get("cache-control", "").lower()
    vary = resp.headers.get("vary", "").lower()
    assert "accept" in vary


def test_spa_does_not_escape_dist(client):
    resp = client.get("/../opencam/main.py", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "def health" not in resp.text
    assert "open-cam" in resp.text
