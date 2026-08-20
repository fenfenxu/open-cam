"""事件 API 冒烟：TestClient + tmp_path 数据库，走 API 建摄像头/规则/查事件/ack。

事件本体直接写库（pipeline 触发见 test_pipeline_e2e.py），这里验证 API 面。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
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


def _insert_event(camera_id: int, rule_type: str = "zone_intrusion",
                  source_offset: float | None = None) -> int:
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type=rule_type, confidence=0.9,
                      detail={"count": 1}, source_offset=source_offset)
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def _write_tiny_mp4(path: Path, frames: int = 30, fps: int = 10) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (160, 120))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(frame, (i * 4, 40), (i * 4 + 20, 80), (0, 255, 0), -1)
        writer.write(frame)
    writer.release()


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


def test_event_disposition_flow(client):
    """处置闭环：状态流转/星标/负责人/备注全部留痕。"""
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id)
    person = client.post("/api/people", json={"name": "张三"}).json()

    body = client.get(f"/events/{event_id}").json()
    assert body["status"] == "open"
    assert body["starred"] is False
    assert body["assignee"] is None

    resp = client.patch(f"/events/{event_id}", json={
        "starred": True, "assignee_id": person["id"], "note": "夜班跟进"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["starred"] is True
    assert body["assignee"] == "张三"
    assert body["assignee_id"] == person["id"]
    assert body["note"] == "夜班跟进"

    resp = client.patch(f"/events/{event_id}", json={
        "verdict": "confirmed"})
    assert resp.json()["status"] == "acked"
    assert resp.json()["verdict"] == "confirmed"

    resp = client.patch(f"/events/{event_id}", json={"status": "resolved"})
    assert resp.json()["status"] == "resolved"

    actions = client.get(f"/events/{event_id}/actions").json()
    kinds = [a["action"] for a in actions]
    assert "star" in kinds
    assert "assign" in kinds
    assert "note" in kinds
    assert "verdict" in kinds
    assert kinds.count("status") >= 2

    assert client.patch(f"/events/{event_id}",
                        json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/events/{event_id}",
                        json={"assignee": "自由文本"}).status_code == 400

    assert client.patch("/events/9999", json={"starred": True}).status_code == 404
    assert client.get("/events/9999/actions").status_code == 404


def test_event_status_and_starred_filters(client):
    camera_id = _make_camera(client)
    e1 = _insert_event(camera_id)
    e2 = _insert_event(camera_id, "loitering")
    _insert_event(camera_id)

    client.patch(f"/events/{e1}", json={"starred": True})
    client.patch(f"/events/{e2}", json={"status": "ignored"})

    assert len(client.get("/events", params={"starred": "true"}).json()) == 1
    assert len(client.get("/events", params={"status": "open"}).json()) == 2
    assert len(client.get("/events", params={"status": "ignored"}).json()) == 1
    # ignored 视为已处理：acked 同步置真
    assert client.get(f"/events/{e2}").json()["acked"] is True


def test_event_exposes_camera_and_clip_window(client):
    """列表/详情带摄像头名、文件名、素材窗口，点开才能追溯到哪路哪一段。"""
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id, source_offset=12.5)

    body = client.get(f"/events/{event_id}").json()
    assert body["camera_id"] == camera_id
    assert body["camera_name"] == "测试摄像头"
    assert body["source_filename"] == "nonexistent.mp4"
    assert body["source_offset"] == pytest.approx(12.5)
    assert body["clip_start"] == pytest.approx(10.5)
    assert body["clip_end"] == pytest.approx(15.5)

    listed = client.get("/events", params={"camera_id": camera_id}).json()
    assert listed[0]["camera_name"] == "测试摄像头"
    assert listed[0]["source_offset"] == pytest.approx(12.5)


def test_event_clip_replays_source_segment(client, tmp_path):
    video = tmp_path / "scene.mp4"
    _write_tiny_mp4(video)
    camera_id = client.post("/cameras", json={
        "name": "回放摄像头", "source_type": "file", "source_uri": str(video),
    }).json()["id"]
    event_id = _insert_event(camera_id, source_offset=1.2)

    resp = client.get(f"/events/{event_id}/clip")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("video/")
    assert len(resp.content) > 100

    bare = _insert_event(camera_id, source_offset=None)
    assert client.get(f"/events/{bare}/clip").status_code == 404
    assert client.get("/events/9999/clip").status_code == 404


def test_rtsp_event_has_camera_name_but_no_clip(client):
    camera_id = client.post("/cameras", json={
        "name": "门口枪机", "source_type": "rtsp",
        "source_uri": "rtsp://127.0.0.1:8554/test",
    }).json()["id"]
    event_id = _insert_event(camera_id)

    body = client.get(f"/events/{event_id}").json()
    assert body["camera_name"] == "门口枪机"
    assert body["source_filename"] is None
    assert body["source_offset"] is None
    assert client.get(f"/events/{event_id}/clip").status_code == 404


def test_ack_writes_status_and_action(client):
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id)

    resp = client.post(f"/events/{event_id}/ack")
    assert resp.json()["status"] == "acked"
    assert resp.json()["acked"] is True

    actions = client.get(f"/events/{event_id}/actions").json()
    assert [a["action"] for a in actions] == ["ack"]


def test_rule_default_intent_line_crossing_observe(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "line_crossing",
        "params": {"line": [[0, 120], [320, 120]], "direction": "both"},
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["intent"] == "observe"
    assert resp.json()["escalate"] == {}


def test_rule_default_intent_intrusion_alert(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["intent"] == "alert"


def test_rule_rejects_bad_intent(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "intent": "banana",
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 422


def test_rule_rejects_bad_escalate(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "escalate": {"mode": "nope"},
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 400, resp.text
    assert "escalate" in str(resp.json()["detail"])
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "escalate": {"mode": "immediate",
                     "compound": {"metric": "foo", "op": "gte", "value": 1}},
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 400, resp.text
    assert "escalate" in str(resp.json()["detail"])


def test_events_needs_action_filter(client):
    camera_id = _make_camera(client)
    session = get_session()
    try:
        todo = Event(
            camera_id=camera_id, type="zone_intrusion", confidence=0.9,
            intent="alert", needs_action=True, status="open", detail={})
        obs = Event(
            camera_id=camera_id, type="line_crossing", confidence=0.9,
            intent="observe", needs_action=False, status="logged", detail={})
        session.add_all([todo, obs])
        session.commit()
        todo_id, obs_id = todo.id, obs.id
    finally:
        session.close()
    ids = {e["id"] for e in client.get("/events").json()}
    assert todo_id in ids and obs_id in ids
    only_todo = client.get("/events", params={"needs_action": True}).json()
    assert {e["id"] for e in only_todo} == {todo_id}
    only_obs = client.get("/events", params={"needs_action": False}).json()
    assert {e["id"] for e in only_obs} == {obs_id}


def test_verdict_false_alarm_sets_ignored(client):
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id)

    resp = client.patch(f"/events/{event_id}", json={"verdict": "false_alarm"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "false_alarm"
    assert body["status"] == "ignored"
    assert body["acked"] is True

    actions = client.get(f"/events/{event_id}/actions").json()
    assert [a["action"] for a in actions] == ["verdict", "status"]


def test_resolved_requires_confirmed_verdict(client):
    camera_id = _make_camera(client)
    event_id = _insert_event(camera_id)

    resp = client.patch(f"/events/{event_id}", json={"status": "resolved"})
    assert resp.status_code == 400, resp.text
    assert "属实" in resp.json()["detail"]


def test_observe_event_cannot_be_disposed(client):
    camera_id = _make_camera(client)
    session = get_session()
    try:
        event = Event(
            camera_id=camera_id, type="line_crossing", confidence=0.9,
            intent="observe", needs_action=False, status="logged", detail={})
        session.add(event)
        session.commit()
        event_id = event.id
    finally:
        session.close()

    assert client.patch(f"/events/{event_id}",
                        json={"status": "acked"}).status_code == 400
    assert client.patch(f"/events/{event_id}",
                        json={"verdict": "confirmed"}).status_code == 400
    assert client.patch(f"/events/{event_id}",
                        json={"starred": True}).status_code == 200
