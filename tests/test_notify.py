"""通知：个人渠道 + 群机器人；仅待办推送。"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from opencam import notify
from opencam.db import get_session
from opencam.models import Event, EventAction


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _insert_todo(camera_id: int, rule_type: str = "zone_intrusion",
                 *, needs_action: bool = True) -> int:
    session = get_session()
    try:
        event = Event(
            camera_id=camera_id, type=rule_type, confidence=0.9,
            intent="alert" if needs_action else "observe",
            needs_action=needs_action,
            status="open" if needs_action else "logged",
            detail={"count": 1})
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def _actions(event_id: int) -> list[EventAction]:
    session = get_session()
    try:
        return (session.query(EventAction).filter_by(event_id=event_id)
                .order_by(EventAction.id.asc()).all())
    finally:
        session.close()


def test_channel_crud(client):
    resp = client.post("/api/notify-channels", json={
        "name": "店长", "webhook": "https://example.com/hook"})
    assert resp.status_code == 201, resp.text
    ch = resp.json()
    assert ch["enabled"] is True
    assert ch["camera_id"] is None

    assert len(client.get("/api/notify-channels").json()) == 1

    resp = client.patch(f"/api/notify-channels/{ch['id']}",
                        json={"enabled": False, "rule_type": "loitering"})
    assert resp.json()["enabled"] is False
    assert resp.json()["rule_type"] == "loitering"

    assert client.delete(f"/api/notify-channels/{ch['id']}").status_code == 204
    assert client.get("/api/notify-channels").json() == []


def test_channel_test_endpoint(client, monkeypatch):
    calls = []

    def fake_send(client_, url, payload):
        calls.append((url, payload))

    monkeypatch.setattr("opencam.api.notify.send_webhook", fake_send)
    ch = client.post("/api/notify-channels", json={
        "name": "值班群", "webhook": "https://example.com/hook"}).json()

    resp = client.post(f"/api/notify-channels/{ch['id']}/test")
    assert resp.json()["ok"] is True
    assert calls and calls[0][0] == "https://example.com/hook"


def test_notify_person_and_group_channels(client, monkeypatch):
    posts: list[str] = []

    def fake_send(client_, url, payload):
        posts.append(url)
        assert payload.get("needs_action") is True
        assert "assignee_id" in payload

    monkeypatch.setattr(notify, "send_webhook", fake_send)

    p_low = client.post("/api/people", json={"name": "甲"}).json()
    p_high = client.post("/api/people", json={"name": "乙"}).json()
    client.post(f"/api/people/{p_low['id']}/channels", json={
        "kind": "feishu", "webhook": "https://example.com/p-low"})
    client.post(f"/api/people/{p_high['id']}/channels", json={
        "kind": "dingtalk", "webhook": "https://example.com/p-high"})
    client.post("/api/event-routings", json={"person_id": p_low["id"]})
    client.post("/api/event-routings", json={"person_id": p_high["id"]})
    client.post("/api/notify-channels", json={
        "name": "群兜底", "webhook": "https://example.com/group"})

    event_id = _insert_todo(camera_id=1)
    assert notify.notify_event(event_id) == 3
    assert posts == [
        "https://example.com/p-low",
        "https://example.com/p-high",
        "https://example.com/group",
    ]

    session = get_session()
    try:
        event = session.get(Event, event_id)
        assert event is not None
        assert event.assignee_id == p_low["id"]
        assert event.assignee == "甲"
    finally:
        session.close()


def test_person_channel_failure_still_pushes_group(client, monkeypatch):
    def flaky_send(client_, url, payload):
        if "personal" in url:
            raise RuntimeError("502 bad gateway")

    monkeypatch.setattr(notify, "send_webhook", flaky_send)

    person = client.post("/api/people", json={"name": "值班"}).json()
    client.post(f"/api/people/{person['id']}/channels", json={
        "kind": "feishu", "webhook": "https://example.com/personal"})
    client.post("/api/event-routings", json={"person_id": person["id"]})
    client.post("/api/notify-channels", json={
        "name": "群", "webhook": "https://example.com/group"})

    event_id = _insert_todo(camera_id=1)
    assert notify.notify_event(event_id) == 2
    actions = _actions(event_id)
    assert len(actions) == 2
    assert actions[0].payload["ok"] is False
    assert actions[1].payload["ok"] is True


def test_notify_skips_non_todo(client, monkeypatch):
    posts = []

    def fake_send(client_, url, payload):
        posts.append(url)

    monkeypatch.setattr(notify, "send_webhook", fake_send)
    client.post("/api/notify-channels", json={
        "name": "群", "webhook": "https://example.com/group"})

    event_id = _insert_todo(camera_id=1, needs_action=False)
    assert notify.notify_event(event_id) == 0
    assert posts == []


def test_resend_notify_requires_todo(client, monkeypatch):
    monkeypatch.setattr("opencam.api.events.notifier.submit", lambda eid: None)
    todo_id = _insert_todo(camera_id=1)
    obs_id = _insert_todo(camera_id=1, needs_action=False)

    assert client.post(f"/api/events/{todo_id}/notify").status_code == 200
    assert client.post(f"/api/events/{obs_id}/notify").status_code == 400
    assert client.post("/api/events/9999/notify").status_code == 404
