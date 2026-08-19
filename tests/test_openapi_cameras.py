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
    assert "/cameras/{camera_id}/live.mjpg" in paths
    assert "/cameras/{camera_id}/source" in paths
