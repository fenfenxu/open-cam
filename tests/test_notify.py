"""通知渠道与推送测试：渠道 CRUD、匹配规则、推送结果留痕。

webhook 发送一律 monkeypatch 掉，不依赖真实网络。
"""

from __future__ import annotations

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
    assert client.patch("/api/notify-channels/999",
                        json={"enabled": False}).status_code == 404

    # 非法规则类型被拒绝
    assert client.post("/api/notify-channels", json={
        "name": "x", "webhook": "https://example.com",
        "rule_type": "bogus"}).status_code == 422


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

    def boom(client_, url, payload):
        raise RuntimeError("连接超时")

    monkeypatch.setattr("opencam.api.notify.send_webhook", boom)
    resp = client.post(f"/api/notify-channels/{ch['id']}/test")
    assert resp.json()["ok"] is False
    assert "连接超时" in resp.json()["error"]


def test_notify_event_matching_and_logging(client, monkeypatch):
    sent = []

    def fake_send(client_, url, payload):
        sent.append((url, payload))

    monkeypatch.setattr(notify, "send_webhook", fake_send)

    # 通配渠道 + 只匹配 loitering 的渠道 + 停用渠道
    client.post("/api/notify-channels", json={
        "name": "全部", "webhook": "https://example.com/all"})
    client.post("/api/notify-channels", json={
        "name": "徘徊专线", "webhook": "https://example.com/loitering",
        "rule_type": "loitering"})
    client.post("/api/notify-channels", json={
        "name": "停用", "webhook": "https://example.com/off", "enabled": False})

    # 入侵事件：只有通配渠道命中
    e1 = _insert_event(camera_id=1, rule_type="zone_intrusion")
    assert notify.notify_event(e1) == 1
    assert [u for u, _ in sent] == ["https://example.com/all"]
    actions = _actions(e1)
    assert len(actions) == 1
    assert actions[0].action == "notify"
    assert actions[0].actor == "全部"
    assert actions[0].payload["ok"] is True

    # 徘徊事件：通配 + 专线都命中
    sent.clear()
    e2 = _insert_event(camera_id=1, rule_type="loitering")
    assert notify.notify_event(e2) == 2
    assert len(sent) == 2
    assert len(_actions(e2)) == 2


def test_notify_event_failure_logged(client, monkeypatch):
    def boom(client_, url, payload):
        raise RuntimeError("502 bad gateway")

    monkeypatch.setattr(notify, "send_webhook", boom)
    client.post("/api/notify-channels", json={
        "name": "不稳定渠道", "webhook": "https://example.com/flaky"})

    event_id = _insert_event(camera_id=1)
    assert notify.notify_event(event_id) == 1
    actions = _actions(event_id)
    assert actions[0].payload["ok"] is False
    assert "502" in actions[0].payload["error"]


def test_resend_notify_endpoint(client, monkeypatch):
    submitted = []
    monkeypatch.setattr("opencam.api.events.notifier.submit",
                        lambda eid: submitted.append(eid))
    event_id = _insert_event(camera_id=1)
    resp = client.post(f"/events/{event_id}/notify")
    assert resp.status_code == 200
    assert submitted == [event_id]
    assert client.post("/events/9999/notify").status_code == 404
