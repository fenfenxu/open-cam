"""事件 API 冒烟：TestClient + tmp_path 数据库，走 API 建摄像头/规则/查事件/ack。

事件本体直接写库（pipeline 触发见 test_pipeline_e2e.py），这里验证 API 面。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import Event


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _make_camera(client) -> int:
    resp = client.post("/cameras", json={
        "name": "测试摄像头", "source_type": "file",
        "source_uri": "/tmp/nonexistent.mp4",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _insert_event(camera_id: int, rule_type: str = "zone_intrusion") -> int:
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type=rule_type, confidence=0.9,
                      detail={"count": 1})
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def test_camera_and_rule_crud(client):
    camera_id = _make_camera(client)

    # 规则 CRUD
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "params": {"polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]},
        "cooldown": 5,
    })
    assert resp.status_code == 201, resp.text
    rule = resp.json()
    assert rule["camera_id"] == camera_id

    resp = client.get(f"/cameras/{camera_id}/rules")
    assert len(resp.json()) == 1

    resp = client.put(f"/cameras/{camera_id}/rules/{rule['id']}", json={
        "type": "object_count", "params": {"class": "person", "threshold": 2},
        "enabled": False, "cooldown": 10,
    })
    assert resp.status_code == 200
    assert resp.json()["type"] == "object_count"

    resp = client.delete(f"/cameras/{camera_id}/rules/{rule['id']}")
    assert resp.status_code == 204
    assert client.get(f"/cameras/{camera_id}/rules").json() == []


def test_events_query_filters_and_pagination(client):
    camera_id = _make_camera(client)
    for _ in range(3):
        _insert_event(camera_id, "zone_intrusion")
    _insert_event(camera_id, "loitering")

    resp = client.get("/events", params={"camera_id": camera_id})
    assert resp.status_code == 200
    assert len(resp.json()) == 4

    resp = client.get("/events", params={"rule_type": "loitering"})
    assert len(resp.json()) == 1

    resp = client.get("/events", params={"camera_id": camera_id,
                                         "limit": 2, "offset": 1})
    assert len(resp.json()) == 2

    # acked 过滤：全部未确认
    assert len(client.get("/events", params={"acked": "false"}).json()) == 4
    assert client.get("/events", params={"acked": "true"}).json() == []


def test_event_detail_and_ack(client):
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id)

    resp = client.get(f"/events/{event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "zone_intrusion"
    assert body["vlm_status"] == "pending"
    assert body["acked"] is False

    resp = client.post(f"/events/{event_id}/ack")
    assert resp.status_code == 200
    assert resp.json()["acked"] is True

    # 404 路径
    assert client.get("/events/9999").status_code == 404
    assert client.post("/events/9999/ack").status_code == 404


def test_camera_not_found_and_snapshot_unavailable(client):
    assert client.get("/cameras/999").status_code == 404
    camera_id = _make_camera(client)
    # 未运行的摄像头没有帧
    assert client.get(f"/cameras/{camera_id}/snapshot.jpg").status_code == 503
