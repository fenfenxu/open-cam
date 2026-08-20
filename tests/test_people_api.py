"""员工 / 个人 IM 渠道 / 事件路由 API：无登录名员工、路由通配、删除级联。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opencam import notify
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


def _make_todo(camera_id: int, rule_type: str = "zone_intrusion") -> int:
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type=rule_type, confidence=0.9,
                      intent="alert", needs_action=True, status="open",
                      detail={})
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def _event(event_id: int) -> Event:
    session = get_session()
    try:
        return session.get(Event, event_id)
    finally:
        session.close()


def test_person_without_login_name(client):
    # 员工可无登录名：不登录也能当负责人
    resp = client.post("/api/people", json={"name": "张三"})
    assert resp.status_code == 201, resp.text
    person = resp.json()
    assert person["name"] == "张三"
    assert person["login_name"] is None
    assert person["channels"] == []

    listed = client.get("/api/people").json()
    assert [p["id"] for p in listed] == [person["id"]]
    assert client.get(f"/api/people/{person['id']}").json()["name"] == "张三"
    assert client.get("/api/people/999").status_code == 404

    resp = client.patch(f"/api/people/{person['id']}",
                        json={"login_name": "zhangsan"})
    assert resp.json()["login_name"] == "zhangsan"


def test_person_login_name_unique(client):
    assert client.post("/api/people", json={
        "name": "张三", "login_name": "zhangsan"}).status_code == 201
    resp = client.post("/api/people", json={
        "name": "李四", "login_name": "zhangsan"})
    assert resp.status_code == 400, resp.text


def test_person_channel_crud_and_test(client, monkeypatch):
    person = client.post("/api/people", json={"name": "张三"}).json()
    resp = client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "feishu", "webhook": "https://example.com/hook"})
    assert resp.status_code == 201, resp.text
    ch = resp.json()
    assert ch["enabled"] is True
    assert ch["person_id"] == person["id"]

    # 非法渠道类型
    assert client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "slack", "webhook": "https://example.com/x"}).status_code == 422

    calls = []
    monkeypatch.setattr("opencam.api.people.send_webhook",
                        lambda c, url, payload: calls.append(url))
    resp = client.post(
        f"/api/people/{person['id']}/channels/{ch['id']}/test")
    assert resp.json()["ok"] is True
    assert calls == ["https://example.com/hook"]

    resp = client.patch(f"/api/people/{person['id']}/channels/{ch['id']}",
                        json={"enabled": False})
    assert resp.json()["enabled"] is False
    assert len(client.get(f"/api/people/{person['id']}/channels").json()) == 1
    assert client.delete(
        f"/api/people/{person['id']}/channels/{ch['id']}").status_code == 204
    assert client.get(f"/api/people/{person['id']}/channels").json() == []


def test_event_routing_crud(client):
    person = client.post("/api/people", json={"name": "张三"}).json()
    # 空 camera_id / rule_type = 通配
    resp = client.post("/api/event-routings", json={"person_id": person["id"]})
    assert resp.status_code == 201, resp.text
    routing = resp.json()
    assert routing["camera_id"] is None
    assert routing["rule_type"] is None
    assert routing["enabled"] is True

    resp = client.patch(f"/api/event-routings/{routing['id']}",
                        json={"rule_type": "zone_intrusion", "enabled": False})
    assert resp.json()["rule_type"] == "zone_intrusion"
    assert resp.json()["enabled"] is False
    assert client.get("/api/event-routings").json()[0]["id"] == routing["id"]
    assert client.delete(
        f"/api/event-routings/{routing['id']}").status_code == 204
    assert client.get("/api/event-routings").json() == []

    # 路由必须指向存在的员工
    assert client.post("/api/event-routings",
                       json={"person_id": 999}).status_code == 400


def test_routing_wildcard_assigns_todo(client, monkeypatch):
    """通配路由命中后 assignee_id 有值，assignee 双写员工名。"""
    camera_id = _make_camera(client)
    person = client.post("/api/people", json={"name": "张三"}).json()
    client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "feishu", "webhook": "https://example.com/hook"})
    client.post("/api/event-routings", json={"person_id": person["id"]})
    monkeypatch.setattr(notify, "send_webhook", lambda c, u, p: None)

    event_id = _make_todo(camera_id)
    assert notify.notify_event(event_id) == 1
    event = _event(event_id)
    assert event.assignee_id == person["id"]
    assert event.assignee == "张三"

    # 不匹配的路由（别的摄像头）不指派
    other = client.post("/api/people", json={"name": "李四"}).json()
    client.post("/api/event-routings", json={
        "person_id": other["id"], "camera_id": camera_id + 100})
    event2 = _make_todo(camera_id)
    notify.notify_event(event2)
    assert _event(event2).assignee_id == person["id"]


def test_delete_person_cascades_and_unlinks_events(client):
    """删员工：渠道与路由级联删；事件 assignee_id 置空、保留 assignee 名字。"""
    camera_id = _make_camera(client)
    person = client.post("/api/people", json={"name": "张三"}).json()
    ch = client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "wecom", "webhook": "https://example.com/hook"}).json()
    routing = client.post("/api/event-routings",
                          json={"person_id": person["id"]}).json()
    event_id = _make_todo(camera_id)
    resp = client.patch(f"/events/{event_id}",
                        json={"assignee_id": person["id"]})
    assert resp.status_code == 200, resp.text

    assert client.delete(f"/api/people/{person['id']}").status_code == 204
    assert client.get("/api/people").json() == []
    assert client.get("/api/event-routings").json() == []

    event = _event(event_id)
    assert event.assignee_id is None
    assert event.assignee == "张三"
    # 路由/渠道 API 对已删员工 404
    assert client.get(
        f"/api/people/{person['id']}/channels").status_code == 404
    assert ch["id"] and routing["id"]  # 曾存在
