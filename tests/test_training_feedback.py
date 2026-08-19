"""事件误报/漏报反馈飞轮：快照归入训练任务数据集。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session, init_db
from opencam.models import Event
from opencam.training.storage import load_samples, task_dir


@pytest.fixture()
def client(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _confirm_task(client, task_id: str) -> None:
    resp = client.post("/training/tasks", json={
        "goal": "垃圾桶快满了就提醒我",
        "confirm": True,
        "task_id": task_id,
        "definition": {
            "object": "垃圾桶",
            "property": "满溢状态",
            "classes": ["空/正常", "将满", "满溢"],
            "rule": {"type": "state_alert", "trigger": "满溢 持续 5 分钟"},
            "region": [[0, 0], [40, 0], [40, 30], [0, 30]],
        },
    })
    assert resp.status_code == 200, resp.text


def _make_camera(client) -> int:
    resp = client.post("/cameras", json={
        "name": "测试摄像头", "source_type": "file",
        "source_uri": "/tmp/nonexistent.mp4",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _insert_event_with_snapshot(tmp_path, camera_id: int) -> int:
    snap = tmp_path / "evt.jpg"
    frame = np.zeros((60, 80, 3), dtype=np.uint8)
    frame[:] = (0, 0, 200)
    assert cv2.imwrite(str(snap), frame)
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type="zone_intrusion",
                      confidence=0.9, detail={}, snapshot_path=str(snap))
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def test_list_and_get_training_tasks(client):
    assert client.get("/training/tasks").json() == []
    _confirm_task(client, "list-me")
    items = client.get("/training/tasks").json()
    assert any(t["task_id"] == "list-me" for t in items)
    body = client.get("/training/tasks/list-me").json()
    assert body["status"] == "confirmed"
    assert body["definition"]["object"] == "垃圾桶"
    assert "train" in body


def test_save_region_and_preview(client, tmp_path):
    _confirm_task(client, "roi")
    resp = client.put("/training/tasks/roi/region", json={
        "region": [[1, 1], [10, 1], [10, 10], [1, 10]],
    })
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["region"]) == 4
    assert client.get("/training/tasks/roi/preview.jpg").status_code == 404


def test_false_alarm_feedback_enters_dataset(client, tmp_path):
    _confirm_task(client, "fly")
    camera_id = _make_camera(client)
    event_id = _insert_event_with_snapshot(tmp_path, camera_id)

    resp = client.post(f"/events/{event_id}/feedback", json={
        "task_id": "fly", "kind": "false_alarm",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["acked"] is True
    sample = body["sample"]
    assert sample["source"] == "feedback"
    assert sample["kind"] == "false_alarm"
    assert sample["label"] == "空/正常"
    dest = task_dir("fly") / "dataset" / "空/正常" / f"{sample['id']}.jpg"
    assert dest.is_file()

    again = client.post(f"/events/{event_id}/feedback", json={
        "task_id": "fly", "kind": "false_alarm",
    })
    assert again.status_code == 200
    assert again.json()["sample"]["already"] is True
    samples = load_samples("fly")
    assert sum(1 for s in samples if s.get("kind") == "false_alarm") == 1


def test_miss_feedback_uses_alert_class(client, tmp_path):
    _confirm_task(client, "miss-fly")
    camera_id = _make_camera(client)
    event_id = _insert_event_with_snapshot(tmp_path, camera_id)
    resp = client.post(f"/events/{event_id}/feedback", json={
        "task_id": "miss-fly", "kind": "miss",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["sample"]["label"] == "满溢"
    dest = task_dir("miss-fly") / "dataset" / "满溢"
    assert any(dest.glob("*.jpg"))


def test_feedback_without_snapshot_is_404(client):
    _confirm_task(client, "no-snap")
    camera_id = _make_camera(client)
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type="loitering", confidence=0.5)
        session.add(event)
        session.commit()
        event_id = event.id
    finally:
        session.close()
    resp = client.post(f"/events/{event_id}/feedback", json={
        "task_id": "no-snap", "kind": "false_alarm",
    })
    assert resp.status_code == 404


def test_feedback_unknown_task_is_404(client, tmp_path):
    camera_id = _make_camera(client)
    event_id = _insert_event_with_snapshot(tmp_path, camera_id)
    resp = client.post(f"/events/{event_id}/feedback", json={
        "task_id": "ghost", "kind": "miss",
    })
    assert resp.status_code == 404
