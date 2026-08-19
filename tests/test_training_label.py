"""VLM 自动标注 + 人工确认队列。测试注入 label_fn / mock，不访问网络、不加载 YOLO。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import TrainingSample, TrainingTask
from opencam.training.crop import crop_frame
from opencam.training.pipeline import run_labeling
from opencam.training.storage import dataset_dir, frames_dir
from opencam.training.vlm_label import parse_label_response, resolve_label_vlm

CLASSES = ["空/正常", "将满", "满溢"]
DEFINITION = {
    "object": "垃圾桶",
    "property": "满溢状态",
    "classes": CLASSES,
}


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _write_frame(path: Path, color=(0, 255, 0)) -> None:
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[10:90, 20:180] = color
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)


def _insert_task(region=None, vlm_config=None, threshold=0.8) -> int:
    session = get_session()
    try:
        task = TrainingTask(
            goal="垃圾桶快满了就提醒我",
            definition=DEFINITION,
            region=region or {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
            vlm_config=vlm_config or {},
            confidence_threshold=threshold,
        )
        session.add(task)
        session.commit()
        return task.id
    finally:
        session.close()


def test_crop_normalized_xywh():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    frame[0:100, 0:100] = 1
    crop = crop_frame(frame, {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0})
    assert crop.shape[1] == 100
    assert crop.shape[0] == 100
    assert int(crop.max()) == 0  # 右半全黑


def test_parse_label_json_and_closed_set():
    label, conf = parse_label_response(
        '废话 {"label": "满溢", "confidence": 0.91}', CLASSES)
    assert label == "满溢"
    assert conf == pytest.approx(0.91)
    # 不在封闭类别 → 置信度清零，进人工队列
    label, conf = parse_label_response('{"label": "爆炸", "confidence": 0.99}', CLASSES)
    assert label == "爆炸"
    assert conf == 0.0
    assert parse_label_response("不是 JSON", CLASSES) == (None, 0.0)


def test_high_confidence_goes_to_dataset(client, tmp_path, tmp_settings):
    task_id = _insert_task()
    _write_frame(frames_dir(task_id) / "a.jpg")

    def label_fn(image_path, task, cfg):
        return "满溢", 0.95

    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        stats = run_labeling(session, task, label_fn=label_fn)
    finally:
        session.close()

    assert stats["accepted"] == 1
    assert stats["pending_review"] == 0
    dests = list((dataset_dir(task_id) / "满溢").glob("*"))
    assert len(dests) == 1

    resp = client.get(f"/training/tasks/{task_id}/review")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending"] == []
    assert body["stats"]["accepted"] == 1
    assert body["classes"] == CLASSES


def test_low_confidence_review_then_accept_or_skip(client, tmp_settings):
    task_id = _insert_task(threshold=0.8)
    _write_frame(frames_dir(task_id) / "low.jpg", color=(0, 0, 255))
    _write_frame(frames_dir(task_id) / "also.jpg", color=(255, 0, 0))

    def label_fn(image_path, task, cfg):
        return "将满", 0.3

    session = get_session()
    try:
        stats = run_labeling(session, session.get(TrainingTask, task_id),
                             label_fn=label_fn)
    finally:
        session.close()
    assert stats["pending_review"] == 2
    assert stats["accepted"] == 0

    queue = client.get(f"/training/tasks/{task_id}/review").json()
    assert len(queue["pending"]) == 2
    first, second = queue["pending"]
    assert first["crop_url"].startswith("/training/samples/")

    crop = client.get(first["crop_url"])
    assert crop.status_code == 200
    assert crop.headers["content-type"].startswith("image/")

    ok = client.post(
        f"/training/tasks/{task_id}/review/{first['id']}",
        json={"label": "空/正常"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "accepted"
    assert ok.json()["source"] == "human"
    assert list((dataset_dir(task_id) / "空_正常").glob("*"))

    skipped = client.post(
        f"/training/tasks/{task_id}/review/{second['id']}",
        json={"skip": True},
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"

    left = client.get(f"/training/tasks/{task_id}/review").json()
    assert left["pending"] == []
    assert left["stats"]["accepted"] == 1
    assert left["stats"]["skipped"] == 1

    # 非法类别
    extra = _insert_task()
    _write_frame(frames_dir(extra) / "x.jpg")
    session = get_session()
    try:
        run_labeling(session, session.get(TrainingTask, extra),
                     label_fn=lambda *a: ("将满", 0.1))
        sid = session.query(TrainingSample).filter_by(task_id=extra).one().id
    finally:
        session.close()
    bad = client.post(f"/training/tasks/{extra}/review/{sid}", json={"label": "不是类别"})
    assert bad.status_code == 400


def test_task_vlm_config_overrides_global(client, tmp_settings):
    task_id = _insert_task(vlm_config={
        "base_url": "http://127.0.0.1:9/v1",
        "model": "qwen-vl-max",
        "timeout": 12,
        "confidence": 0.55,
    })
    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        cfg = resolve_label_vlm(task)
    finally:
        session.close()
    assert cfg.model == "qwen-vl-max"
    assert cfg.base_url == "http://127.0.0.1:9/v1"
    assert cfg.timeout == 12
    assert cfg.confidence_threshold == pytest.approx(0.55)
    assert tmp_settings.vlm_label_model == "glm-4v-flash"


def test_label_api_uses_injected_vlm(client, tmp_settings, monkeypatch):
    task_id = _insert_task()
    _write_frame(frames_dir(task_id) / "n.jpg")

    def fake_default(*_a, **_k):
        def _fn(image_path, task, cfg):
            assert cfg.model == "glm-4v-flash"
            return "空/正常", 0.99
        return _fn

    monkeypatch.setattr("opencam.training.pipeline._default_label_fn", fake_default)
    resp = client.post(f"/training/tasks/{task_id}/label")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stats"]["accepted"] == 1


def test_label_without_frames_is_400(client, tmp_settings):
    task_id = _insert_task()
    resp = client.post(f"/training/tasks/{task_id}/label")
    assert resp.status_code == 400
    assert client.get("/training/tasks/999/review").status_code == 404
