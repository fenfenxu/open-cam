"""自助模型训练测试：解构 / 抽帧 / 标注分流 / 人工确认 / 训练评估 / 部署回滚。

全部走 mock：不触网、不下载真实模型；VLM 打标用注入的假 labeler，
训练走 OPENCAM_DETECTOR=mock 的占位路径。
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session, init_db
from opencam.models import (MODEL_ARCHIVED, MODEL_DEPLOYED, Rule,
                            TrainingSample, TrainingTask)
from opencam.training import store
from opencam.training.decompose import decompose_goal, normalize_definition
from opencam.training.frames import extract_from_file
from opencam.training.labeling import auto_label, review_sample

W, H = 320, 240
POLYGON = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]  # 全屏


def _make_video(path: Path, frames: int = 40, fps: int = 10) -> None:
    """合成视频：大块矩形，亮度与位置随帧变化（保证去重后两类样本充足，
    亮/暗帧分别模拟 满溢/正常）。"""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (W, H))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        level = 40 + (i * 25) % 216          # 亮度 40-255 循环
        y = 20 + (i * 9) % 100               # 位置缓慢移动
        cv2.rectangle(frame, (60, y), (260, min(y + 100, H - 1)),
                      (level, level, level), -1)
        writer.write(frame)
    writer.release()


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    init_db(tmp_settings.db_url)
    with TestClient(app) as c:
        yield c


# ---------- 语义解构 ----------

def test_decompose_heuristic_fallback(monkeypatch):
    """无 api key 时启发式兜底：垃圾桶满溢样板场景。"""
    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    result = decompose_goal("垃圾桶快满了就提醒我")
    assert result["object_name"] == "垃圾桶"
    assert result["classes"] == ["正常", "满溢"]
    assert result["rule"]["trigger_class"] == "满溢"
    assert result["metrics"]["accuracy"] == 0.90


def test_normalize_definition_bad_input():
    """LLM 输出不规范时的兜底规整。"""
    result = normalize_definition({"classes": ["满"], "rule": {}}, "goal")
    assert result["classes"] == ["正常", "异常"]
    assert result["rule"]["trigger_class"] == "异常"
    assert result["rule"]["duration_s"] == 300


# ---------- 抽帧 ----------

def test_extract_frames_from_file(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    video = tmp_path / "v.mp4"
    _make_video(video)
    saved = extract_from_file(1, str(video), POLYGON, interval_s=0.2,
                              max_frames=20)
    assert len(saved) >= 2
    for path in saved:
        assert path.exists()
        assert str(tmp_path) in str(path)  # 落在隔离数据目录


def test_extract_frames_rejects_bad_video(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    with pytest.raises(ValueError, match="无法打开"):
        extract_from_file(1, str(tmp_path / "missing.mp4"), POLYGON)


# ---------- 标注分流与人工确认 ----------

def _make_task_with_samples(tmp_path, n=4) -> int:
    """造一个任务 + n 张样本图。"""
    session = get_session()
    try:
        task = TrainingTask(
            goal="垃圾桶快满了就提醒我", object_name="垃圾桶",
            property_name="满溢状态", classes=["正常", "满溢"],
            rule={"type": "state_alert", "trigger_class": "满溢",
                  "duration_s": 300},
            metrics={"accuracy": 0.9, "recall": 0.85,
                     "false_alarm_per_day": 2},
            confidence_threshold=0.8)
        session.add(task)
        session.commit()
        frame_dir = store.frames_dir(task.id)
        frame_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img = np.full((60, 80, 3), 255 if i % 2 else 0, dtype=np.uint8)
            path = frame_dir / f"frame_{i:04d}.jpg"
            cv2.imwrite(str(path), img)
            session.add(TrainingSample(task_id=task.id, image_path=str(path)))
        session.commit()
        return task.id
    finally:
        session.close()


def test_auto_label_splits_by_confidence(tmp_settings, tmp_path):
    """高置信自动入数据集，低置信进人工确认队列。"""
    init_db(tmp_settings.db_url)
    task_id = _make_task_with_samples(tmp_path, n=4)

    def fake_labeler(image_path, task):
        # 亮图高置信满溢，暗图低置信正常
        bright = cv2.imread(image_path).mean() > 128
        return ("满溢", 0.95) if bright else ("正常", 0.5)

    stats = auto_label(task_id, labeler=fake_labeler)
    assert stats == {"auto": 2, "pending": 2, "failed": 0}

    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        assert task.status == "labeled"
        samples = session.query(TrainingSample).filter_by(
            task_id=task_id).order_by(TrainingSample.id).all()
        assert [s.status for s in samples] == [
            "pending_review", "auto", "pending_review", "auto"]
        assert samples[1].final_label == "满溢"
    finally:
        session.close()


def test_review_sample_confirm_and_skip(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    task_id = _make_task_with_samples(tmp_path, n=2)
    session = get_session()
    try:
        ids = [s.id for s in session.query(TrainingSample)
               .filter_by(task_id=task_id).all()]
    finally:
        session.close()

    ok = review_sample(task_id, ids[0], "满溢")
    assert ok.status == "confirmed" and ok.final_label == "满溢"
    skipped = review_sample(task_id, ids[1], "skip")
    assert skipped.status == "skipped" and skipped.final_label is None
    with pytest.raises(ValueError, match="标签必须是"):
        review_sample(task_id, ids[0], "不存在的类别")


# ---------- API 全链路 ----------

def test_training_journey_api(client, tmp_settings, tmp_path, monkeypatch):
    """七步旅程端到端：创建 → 确认定义 → 抽帧 → 标注 → 确认 → 训练 →
    报告 → 部署 → 回滚。"""
    video = tmp_path / "v.mp4"
    _make_video(video)

    # 1. 说需求：创建任务（无 key 走启发式解构）
    resp = client.post("/api/training/tasks",
                       json={"goal": "垃圾桶快满了就提醒我"})
    assert resp.status_code == 201
    task = resp.json()
    task_id = task["id"]
    assert task["status"] == "draft"
    assert task["classes"] == ["正常", "满溢"]

    # 2. 确认定义
    resp = client.post(f"/api/training/tasks/{task_id}/definition", json={
        "object_name": "垃圾桶", "property_name": "满溢状态",
        "classes": ["正常", "满溢"],
        "rule": {"trigger_class": "满溢", "duration_s": 300},
        "metrics": {"accuracy": 0.9, "recall": 0.85,
                    "false_alarm_per_day": 2},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    # 3. 抽帧（视频文件 + 全屏区域）
    resp = client.post(f"/api/training/tasks/{task_id}/extract-frames", json={
        "video_path": str(video), "polygon": POLYGON,
        "interval_s": 0.2, "max_frames": 20,
    })
    assert resp.status_code == 200
    task = resp.json()
    assert task["status"] == "extracted"
    total = sum(task["sample_counts"].values())
    assert total >= 4

    # 4. 自动标注：假 VLM（亮=满溢高置信，暗=正常高置信）
    monkeypatch.setenv("OPENCAM_VLM_API_KEY", "test-key")

    def fake_label_image(image_path, object_name, property_name, classes,
                         base_url=None, model=None):
        # 合成视频为黑底 + 大面积灰块：均值 > 30 视为"满溢"（亮块），
        # 否则"正常"（近全黑帧）
        bright = cv2.imread(image_path).mean() > 30
        return ("满溢" if bright else "正常"), 0.95

    import opencam.training.labeling as labeling_mod
    monkeypatch.setattr(labeling_mod, "label_image", fake_label_image)
    resp = client.post(f"/api/training/tasks/{task_id}/auto-label")
    assert resp.status_code == 200
    _wait_status(client, task_id, "labeled")

    # 5. 人工确认队列应为空（全部高置信），手动改两张标签验证 API
    resp = client.get(f"/api/training/tasks/{task_id}/review")
    assert resp.status_code == 200
    assert resp.json() == []

    # 6. 训练（mock 占位，秒回）
    resp = client.post(f"/api/training/tasks/{task_id}/train",
                       json={"epochs": 2})
    assert resp.status_code == 200
    _wait_status(client, task_id, "trained")

    # 报告：mock 固定指标达标 + 人话结论
    resp = client.get(f"/api/training/tasks/{task_id}/report")
    assert resp.status_code == 200
    report = resp.json()
    assert report["passed"] is True
    assert "准确率" in report["conclusion"]
    assert report["metrics"]["accuracy"] >= report["targets"]["accuracy"]

    # 模型版本落盘
    resp = client.get(f"/api/training/tasks/{task_id}/models")
    models = resp.json()
    assert len(models) == 1 and models[0]["version"] == 1
    model_id = models[0]["id"]
    assert Path(store.task_dir(task_id)).exists()

    # 7. 部署：创建 state_classify 规则
    resp = client.post("/cameras", json={
        "name": "后厨", "source_type": "file", "source_uri": str(video)})
    camera_id = resp.json()["id"]
    resp = client.post(f"/api/training/models/{model_id}/deploy",
                       json={"camera_id": camera_id, "duration_s": 300})
    assert resp.status_code == 200
    deployed = resp.json()
    assert deployed["status"] == "deployed"
    rule_id = deployed["rule_id"]
    resp = client.get(f"/cameras/{camera_id}/rules")
    rules = resp.json()
    assert any(r["id"] == rule_id and r["type"] == "state_classify"
               for r in rules)

    # 回滚：规则停用，模型下线
    resp = client.post(f"/api/training/models/{model_id}/rollback")
    assert resp.status_code == 200
    assert resp.json()["status"] == MODEL_ARCHIVED
    session = get_session()
    try:
        assert session.get(Rule, rule_id).enabled is False
    finally:
        session.close()


def test_auto_label_without_key_returns_400(client, tmp_path, monkeypatch):
    """未配置 VLM key 时自动标注给出明确提示。"""
    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    video = tmp_path / "v.mp4"
    _make_video(video)
    task = client.post("/api/training/tasks",
                       json={"goal": "垃圾桶满溢"}).json()
    client.post(f"/api/training/tasks/{task['id']}/definition", json={
        "object_name": "垃圾桶", "property_name": "满溢状态",
        "classes": ["正常", "满溢"], "rule": {}, "metrics": {}})
    client.post(f"/api/training/tasks/{task['id']}/extract-frames", json={
        "video_path": str(video), "polygon": POLYGON, "interval_s": 0.2})
    resp = client.post(f"/api/training/tasks/{task['id']}/auto-label")
    assert resp.status_code == 400
    assert "OPENCAM_VLM_API_KEY" in resp.json()["detail"]


def test_deployed_state_classify_rule_fires(tmp_settings, tmp_path):
    """部署后的 state_classify 规则在流水线里真实触发（mock 分类器：
    亮区域 = 最后一类）。"""
    init_db(tmp_settings.db_url)
    video = tmp_path / "bright.mp4"
    # 全亮视频：mock 分类器必判最后一类
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (W, H))
    for _ in range(30):
        writer.write(np.full((H, W, 3), 255, dtype=np.uint8))
    writer.release()

    from opencam.models import CAMERA_RUNNING, Camera
    session = get_session()
    try:
        camera = Camera(name="亮", source_type="file", source_uri=str(video),
                        status=CAMERA_RUNNING)
        session.add(camera)
        session.commit()
        # 模型文件只需存在（mock 分类器不读内容）
        model_path = tmp_path / "training" / "1" / "models" / "v1"
        model_path.mkdir(parents=True)
        (model_path / "best.pt").write_text("mock weights\n")
        rule = Rule(camera_id=camera.id, type="state_classify", cooldown=1.0,
                    params={
                        "polygon": POLYGON, "classes": ["正常", "满溢"],
                        "trigger_class": "满溢", "duration_s": 0.1,
                        "model_path": str(model_path / "best.pt"),
                        "object_name": "垃圾桶",
                    })
        session.add(rule)
        session.commit()
        camera_id = camera.id
    finally:
        session.close()

    from opencam.models import Event
    from opencam.pipeline import start_camera, stop_camera
    start_camera(camera_id)
    try:
        deadline = time.time() + 15
        events = []
        while time.time() < deadline:
            session = get_session()
            try:
                events = session.query(Event).filter_by(
                    camera_id=camera_id, type="state_classify").all()
            finally:
                session.close()
            if events:
                break
            time.sleep(0.5)
    finally:
        stop_camera(camera_id)

    assert events, "state_classify 规则未产生事件"
    assert events[0].detail["state"] == "满溢"


def _wait_status(client, task_id: int, status: str, timeout: float = 30) -> dict:
    """轮询任务状态直到到达目标状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/training/tasks/{task_id}").json()
        if task["status"] == status:
            return task
        if task["status"] == "failed":
            raise AssertionError(f"任务失败: {task['error']}")
        time.sleep(0.2)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内到达 {status}")
