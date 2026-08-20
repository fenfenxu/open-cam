"""训练任务骨架：语义目标解构 + 感知哈希抽帧。

LLM / 视频解码均可注入或用合成 mp4；不打真实外网。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session, init_db
from opencam.models import Camera, Video
from opencam.training.storage import list_frames, load_definition, task_dir, task_exists


@pytest.fixture()
def client(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_llm_network(monkeypatch):
    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENCAM_VLM_LABEL_API_KEY", raising=False)


def _bin_def():
    return {
        "object": "垃圾桶",
        "property": "满溢状态",
        "classes": ["空/正常", "将满", "满溢"],
        "rule": {"type": "state_alert", "trigger": "满溢 持续 5 分钟"},
        "metrics": {
            "accuracy": 0.90,
            "recall": 0.85,
            "false_alarm_per_day": 2,
        },
    }


def _make_video(path: Path, frames: int, paint) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (80, 60))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        paint(frame, i)
        writer.write(frame)
    writer.release()


def test_parse_definition_extracts_json_and_fills_defaults():
    from opencam.training.define import parse_definition_response

    raw = '前言 {"object": "工服", "property": "合规", "classes": ["合规", "未穿"]}'
    parsed = parse_definition_response(raw)
    assert parsed["object"] == "工服"
    assert parsed["property"] == "合规"
    assert parsed["classes"] == ["合规", "未穿"]
    assert parsed["metrics"]["accuracy"] == pytest.approx(0.90)
    assert parsed["metrics"]["recall"] == pytest.approx(0.85)
    assert parsed["metrics"]["false_alarm_per_day"] == 2
    assert "rule" in parsed


def test_parse_definition_clamps_classes_to_2_4():
    from opencam.training.define import parse_definition_response

    too_few = parse_definition_response(
        '{"object": "x", "property": "y", "classes": ["仅一个"]}')
    assert 2 <= len(too_few["classes"]) <= 4

    too_many = parse_definition_response(
        '{"object": "x", "property": "y",'
        ' "classes": ["a", "b", "c", "d", "e"]}')
    assert too_many["classes"] == ["a", "b", "c", "d"]


def test_perceptual_hash_near_duplicates_have_small_distance():
    from opencam.training.frames import hamming_distance, perceptual_hash

    a = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.rectangle(a, (5, 5), (25, 25), (255, 255, 255), -1)
    b = a.copy()
    b[0, 0] = (11, 21, 31)
    c = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.rectangle(c, (50, 30), (75, 55), (255, 255, 255), -1)
    assert hamming_distance(perceptual_hash(a), perceptual_hash(b)) <= 4
    assert hamming_distance(perceptual_hash(a), perceptual_hash(c)) > 8


def test_create_draft_does_not_write_definition(client, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())

    resp = client.post("/api/training/tasks",
                       json={"goal": "垃圾桶快满了就提醒我"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["definition"]["object"] == "垃圾桶"
    assert 2 <= len(body["definition"]["classes"]) <= 4
    assert "准确率" in body["metrics_explained"]
    task_id = body["task_id"]
    assert not task_exists(task_id)
    assert not (task_dir(task_id) / "definition.json").exists()


def test_create_with_confirm_persists_definition(client, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())

    resp = client.post("/api/training/tasks", json={
        "goal": "垃圾桶快满了就提醒我",
        "confirm": True,
        "task_id": "bin-1",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["task_id"] == "bin-1"
    loaded = load_definition("bin-1")
    assert loaded["object"] == "垃圾桶"
    assert loaded["goal"] == "垃圾桶快满了就提醒我"


def test_confirm_endpoint_writes_user_edited_definition(client, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())

    draft = client.post("/api/training/tasks", json={"goal": "垃圾桶满了提醒"}).json()
    task_id = draft["task_id"]
    edited = dict(draft["definition"])
    edited["classes"] = ["空", "满"]

    resp = client.post(f"/api/training/tasks/{task_id}/confirm",
                       json={"definition": edited})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"
    assert load_definition(task_id)["classes"] == ["空", "满"]


def test_create_without_llm_key_uses_fallback(client):
    resp = client.post("/api/training/tasks", json={"goal": "检测未戴口罩"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["definition"]["object"]
    assert 2 <= len(body["definition"]["classes"]) <= 4


def test_extract_frames_dedups_near_duplicates(client, tmp_path, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())
    client.post("/api/training/tasks", json={
        "goal": "x", "confirm": True, "task_id": "dedup",
    })

    video = tmp_path / "dup.mp4"

    def paint(frame, i):
        # 前半段同一构图，后半段换成另一块区域，感知哈希应分成两簇
        if i < 20:
            cv2.rectangle(frame, (5, 5), (25, 25), (255, 255, 255), -1)
        else:
            cv2.rectangle(frame, (50, 30), (75, 55), (255, 255, 255), -1)

    _make_video(video, frames=40, paint=paint)

    resp = client.post("/api/training/tasks/dedup/frames", json={
        "source_uri": str(video),
        "max_frames": 30,
        "hamming_threshold": 8,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["written"] == 2
    assert body["skipped_dup"] >= 1
    assert len(list_frames("dedup")) == 2


def test_extract_from_camera_file_source(client, tmp_path, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())
    client.post("/api/training/tasks", json={
        "goal": "x", "confirm": True, "task_id": "cam-src",
    })

    video = tmp_path / "cam.mp4"

    def paint(frame, i):
        x = min(60, i * 6)
        cv2.rectangle(frame, (x, 10), (x + 15, 40), (255, 255, 255), -1)

    _make_video(video, frames=12, paint=paint)
    session = get_session()
    try:
        cam = Camera(name="门口", source_type="file", source_uri=str(video))
        session.add(cam)
        session.commit()
        cam_id = cam.id
    finally:
        session.close()

    resp = client.post("/api/training/tasks/cam-src/frames",
                       json={"camera_id": cam_id, "max_frames": 5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] >= 1
    assert list_frames("cam-src")


def test_extract_from_uploaded_video_id(client, tmp_path, monkeypatch):
    from opencam.training import define as define_mod

    monkeypatch.setattr(define_mod, "call_llm_decompose",
                        lambda goal: _bin_def())
    client.post("/api/training/tasks", json={
        "goal": "x", "confirm": True, "task_id": "vid-src",
    })

    video = tmp_path / "up.mp4"

    def paint(frame, i):
        y = min(40, i * 5)
        cv2.rectangle(frame, (10, y), (40, y + 15), (255, 255, 255), -1)

    _make_video(video, frames=8, paint=paint)
    session = get_session()
    try:
        row = Video(filename="up.mp4", path=str(video),
                    size_bytes=video.stat().st_size, created_at=0)
        session.add(row)
        session.commit()
        vid = row.id
    finally:
        session.close()

    resp = client.post("/api/training/tasks/vid-src/frames",
                       json={"video_id": vid, "max_frames": 5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] >= 1


def test_frames_require_confirmed_task(client, tmp_path):
    video = tmp_path / "n.mp4"

    def paint(frame, _i):
        frame[:] = (1, 2, 3)

    _make_video(video, frames=3, paint=paint)
    resp = client.post("/api/training/tasks/missing/frames",
                       json={"source_uri": str(video)})
    assert resp.status_code == 404


def test_openapi_includes_training_skeleton_paths(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "post" in paths["/api/training/tasks"]
    assert "post" in paths["/api/training/tasks/{task_id}/confirm"]
    assert "post" in paths["/api/training/tasks/{task_id}/frames"]
    assert "post" in paths["/api/training/tasks/{task_id}/annotate"]
    assert "get" in paths["/api/training/tasks"]
    assert "get" in paths["/api/training/tasks/{task_id}"]
    assert "put" in paths["/api/training/tasks/{task_id}/region"]
    assert "post" in paths["/api/events/{event_id}/feedback"]
