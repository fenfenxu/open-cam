"""员工与事件路由 API 测试。"""

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
    resp = client.post("/api/cameras", json={
        "name": "测试摄像头", "source_type": "file",
        "source_uri": "/tmp/nonexistent.mp4",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_person_without_login_name(client):
    resp = client.post("/api/people", json={"name": "夜班小王"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "夜班小王"
    assert body["login_name"] is None


def test_routing_wildcard_matches(client):
    p1 = client.post("/api/people", json={"name": "甲"}).json()
    p2 = client.post("/api/people", json={"name": "乙"}).json()
    client.post("/api/event-routings", json={"person_id": p1["id"]})
    client.post("/api/event-routings", json={
        "person_id": p2["id"], "rule_type": "zone_intrusion"})

    routings = client.get("/api/event-routings").json()
    assert len(routings) == 2
    assert routings[0]["person_id"] == p1["id"]
    assert routings[0]["camera_id"] is None
    assert routings[0]["rule_type"] is None


def test_delete_person_clears_assignee_id_keeps_name(client):
    camera_id = _make_camera(client)
    person = client.post("/api/people", json={"name": "张三"}).json()
    session = get_session()
    try:
        event = Event(
            camera_id=camera_id, type="zone_intrusion", confidence=0.9,
            intent="alert", needs_action=True, status="open",
            assignee_id=person["id"], assignee="张三", detail={})
        session.add(event)
        session.commit()
        event_id = event.id
    finally:
        session.close()

    assert client.delete(f"/api/people/{person['id']}").status_code == 204
    body = client.get(f"/api/events/{event_id}").json()
    assert body["assignee_id"] is None
    assert body["assignee"] == "张三"


def test_person_channel_crud(client):
    person = client.post("/api/people", json={"name": "李四"}).json()
    resp = client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "feishu", "webhook": "https://example.com/personal"})
    assert resp.status_code == 201, resp.text
    ch = resp.json()
    assert ch["enabled"] is True

    listed = client.get(f"/api/people/{person['id']}/channels").json()
    assert len(listed) == 1

    resp = client.patch(
        f"/api/people/{person['id']}/channels/{ch['id']}",
        json={"enabled": False})
    assert resp.json()["enabled"] is False

    assert client.delete(
        f"/api/people/{person['id']}/channels/{ch['id']}").status_code == 204
