"""摄像头管理 API：更新 409 语义、级联删除、health 形状、重连与批量启停。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import CAMERA_RUNNING, Camera, Event, Rule


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _make_camera(client, **kw) -> dict:
    body = {"name": "测试摄像头", "source_type": "file",
            "source_uri": "/tmp/nonexistent.mp4", **kw}
    resp = client.post("/cameras", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_stopped_camera_health_is_null(client):
    cam = _make_camera(client)
    got = client.get(f"/cameras/{cam['id']}").json()
    assert got["health"] is None
    listed = client.get("/cameras").json()
    assert listed[0]["health"] is None


def test_put_rename_while_running(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        row = session.get(Camera, cam["id"])
        row.status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    resp = client.put(f"/cameras/{cam['id']}", json={"name": "新名称"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "新名称"


def test_put_source_while_running_conflict(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        row = session.get(Camera, cam["id"])
        row.status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    resp = client.put(f"/cameras/{cam['id']}", json={"source_uri": "/tmp/other.mp4"})
    assert resp.status_code == 409
    assert "请先停止摄像头再修改视频源" in resp.json()["detail"]
    assert client.get(f"/cameras/{cam['id']}").json()["source_uri"] == cam["source_uri"]


def test_put_source_after_stop(client):
    cam = _make_camera(client)
    resp = client.put(f"/cameras/{cam['id']}", json={"source_uri": "/tmp/other.mp4"})
    assert resp.status_code == 200
    assert resp.json()["source_uri"] == "/tmp/other.mp4"


def test_put_empty_body_unprocessable(client):
    cam = _make_camera(client)
    resp = client.put(f"/cameras/{cam['id']}", json={})
    assert resp.status_code == 422


def test_camera_not_found(client):
    assert client.get("/cameras/999").status_code == 404
    assert client.put("/cameras/999", json={"name": "x"}).status_code == 404
    assert client.delete("/cameras/999").status_code == 404


def test_delete_cascades_rules_events_snapshots_keeps_uploads(client, tmp_settings):
    from opencam.config import settings

    video = client.post("/cameras/upload",
                        files={"file": ("keep.mp4", b"abc", "video/mp4")}).json()
    cam = _make_camera(client, source_uri=video["path"])
    camera_id = cam["id"]

    session = get_session()
    try:
        rule = Rule(camera_id=camera_id, name="入侵", type="zone_intrusion",
                    params={}, enabled=True, cooldown=5)
        session.add(rule)
        session.commit()
        snap = settings.snapshot_dir
        snap.mkdir(parents=True, exist_ok=True)
        snap_file = snap / f"cam{camera_id}_test.jpg"
        snap_file.write_bytes(b"jpeg")
        event = Event(camera_id=camera_id, rule_id=rule.id, type="zone_intrusion",
                      confidence=0.9, snapshot_path=str(snap_file), detail={})
        session.add(event)
        session.commit()
    finally:
        session.close()

    resp = client.delete(f"/cameras/{camera_id}")
    assert resp.status_code == 204
    session = get_session()
    try:
        assert session.query(Rule).filter_by(camera_id=camera_id).count() == 0
        assert session.query(Event).filter_by(camera_id=camera_id).count() == 0
        assert session.get(Camera, camera_id) is None
    finally:
        session.close()
    assert not snap_file.exists()
    assert Path(video["path"]).exists()


def test_reconnect_stopped_conflict(client):
    cam = _make_camera(client)
    resp = client.post(f"/cameras/{cam['id']}/reconnect")
    assert resp.status_code == 409
    assert "仅运行中的摄像头可以重连" in resp.json()["detail"]


def test_reconnect_running_ok(client):
    cam = _make_camera(client)
    session = get_session()
    try:
        session.get(Camera, cam["id"]).status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()
    # 无真实采集线程时 start_camera 仍会把 status 设回 running（文件打不开也不抛）
    resp = client.post(f"/cameras/{cam['id']}/reconnect")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"


def test_batch_start_partial(client):
    cam = _make_camera(client)
    resp = client.post("/cameras/batch/start", json={"ids": [cam["id"], 999]})
    assert resp.status_code == 200, resp.text
    results = {item["id"]: item for item in resp.json()["results"]}
    assert results[cam["id"]]["ok"] is True
    assert results[999]["ok"] is False
    assert "摄像头不存在" in results[999]["error"]


def test_batch_stop_idempotent(client):
    cam = _make_camera(client)
    resp = client.post("/cameras/batch/stop", json={"ids": [cam["id"]]})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["ok"] is True


def test_batch_empty_ids_unprocessable(client):
    resp = client.post("/cameras/batch/start", json={"ids": []})
    assert resp.status_code == 422
