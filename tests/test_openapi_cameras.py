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
    assert "/api/videos" in paths
    assert "get" in paths["/api/videos"] and "post" in paths["/api/videos"]
    assert "/api/videos/{video_id}" in paths
    assert "put" in paths["/api/cameras/{camera_id}"]
    assert "/api/cameras/batch/start" in paths
    assert "/api/cameras/batch/stop" in paths
    assert "/api/cameras/{camera_id}/reconnect" in paths
    assert "/api/cameras/{camera_id}/live.mjpg" in paths
    assert "/api/cameras/{camera_id}/source" in paths


def test_rest_namespace_does_not_collide_with_page_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in paths:
        assert path in {"/health"} or path == "/api" or path.startswith("/api/"), path
    assert "/cameras" not in paths
    assert "/events" not in paths
    assert "/videos" not in paths
    assert "/models" not in paths
    assert "/training/tasks" not in paths
