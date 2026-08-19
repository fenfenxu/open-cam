"""VLM 自动标注流水线 + 人工确认队列。

测试不访问真实 VLM：打标函数可注入；API 走 TestClient + 临时数据目录。
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.training.crop import crop_polygon
from opencam.training.label import (
    parse_label_response,
    resolve_vlm_config,
    route_sample,
)
from opencam.training.storage import (
    load_samples,
    save_definition,
    task_dir,
    write_frame,
)


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_vlm_network(monkeypatch):
    """本文件测试默认不带 VLM key，避免误打外网。"""
    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENCAM_VLM_LABEL_API_KEY", raising=False)


def _solid_frame(w=80, h=60, color=(0, 0, 255)) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def test_crop_polygon_uses_axis_aligned_bbox():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    frame[10:40, 20:80] = (0, 255, 0)
    crop = crop_polygon(frame, [[20, 10], [80, 10], [80, 40], [20, 40]])
    assert crop.shape[0] == 30
    assert crop.shape[1] == 60
    assert (crop[0, 0] == (0, 255, 0)).all()


def test_crop_polygon_clips_to_frame_and_rejects_empty():
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    crop = crop_polygon(frame, [[40, 40], [80, 40], [80, 80], [40, 80]])
    assert crop.shape[0] > 0 and crop.shape[1] > 0

    with pytest.raises(ValueError):
        crop_polygon(frame, [])


def test_parse_label_response_extracts_json_and_rejects_unknown_class():
    classes = ["空/正常", "将满", "满溢"]
    label, conf, reason = parse_label_response(
        '前言 {"label": "满溢", "confidence": 0.91, "reason": "堆到桶口"}',
        classes,
    )
    assert label == "满溢"
    assert conf == pytest.approx(0.91)
    assert "桶口" in reason

    label, conf, _ = parse_label_response(
        '{"label": "未知", "confidence": 0.99}', classes)
    assert label is None
    assert conf == 0.0


def test_resolve_vlm_config_task_overrides_global(tmp_settings, monkeypatch):
    monkeypatch.setattr(tmp_settings, "vlm_label_base_url",
                        "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setattr(tmp_settings, "vlm_label_model", "glm-4v-flash")
    monkeypatch.setattr(tmp_settings, "vlm_label_timeout", 30.0)
    monkeypatch.setenv("OPENCAM_VLM_API_KEY", "global-key")

    cfg = resolve_vlm_config({})
    assert cfg.model == "glm-4v-flash"
    assert "bigmodel" in cfg.base_url
    assert cfg.api_key == "global-key"

    cfg = resolve_vlm_config({
        "vlm": {
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen2.5-vl",
            "timeout": 12,
        },
    })
    assert cfg.base_url == "http://127.0.0.1:11434/v1"
    assert cfg.model == "qwen2.5-vl"
    assert cfg.timeout == 12
    # api_key 仍只走环境变量，任务定义不能覆盖
    assert cfg.api_key == "global-key"


def test_route_sample_splits_high_and_low_confidence():
    classes = ["空/正常", "满溢"]
    assert route_sample("满溢", 0.9, classes, 0.8) == "auto"
    assert route_sample("满溢", 0.5, classes, 0.8) == "review"
    assert route_sample(None, 0.99, classes, 0.8) == "review"
    assert route_sample("满溢", 0.8, classes, 0.8) == "auto"


def _seed_task(task_id: str, n_frames: int = 3) -> Path:
    """写入 definition.json 与若干抽帧，供标注流水线消费。"""
    save_definition(task_id, {
        "object": "垃圾桶",
        "property": "满溢状态",
        "classes": ["空/正常", "将满", "满溢"],
        "region": [[0, 0], [40, 0], [40, 30], [0, 30]],
        "confidence_threshold": 0.8,
    })
    root = task_dir(task_id)
    for i in range(n_frames):
        write_frame(task_id, f"{i:04d}.jpg", _solid_frame())
    return root


def test_annotate_high_confidence_enters_dataset_low_enters_review(
        tmp_settings):
    from opencam.training.label import annotate_task

    _seed_task("t1", n_frames=3)

    def fake_label(_image_bytes, _definition, _cfg):
        # 按调用次序：高 / 低 / 高
        fake_label.n += 1
        if fake_label.n == 2:
            return "将满", 0.4, "看不清"
        return "满溢", 0.95, "堆满"
    fake_label.n = 0

    result = annotate_task("t1", label_fn=fake_label)
    assert result["auto"] == 2
    assert result["review"] == 1

    samples = load_samples("t1")
    statuses = [s["status"] for s in samples]
    assert statuses.count("auto") == 2
    assert statuses.count("review") == 1

    dataset = task_dir("t1") / "dataset" / "满溢"
    assert len(list(dataset.glob("*.jpg"))) == 2
    # 低置信样本还不能进训练集
    pending_cls = task_dir("t1") / "dataset" / "将满"
    assert not pending_cls.exists() or list(pending_cls.glob("*.jpg")) == []


def test_annotate_without_api_key_sends_all_to_review(
        tmp_settings, monkeypatch):
    from opencam.training.label import annotate_task

    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENCAM_VLM_LABEL_API_KEY", raising=False)
    _seed_task("t-nokey", n_frames=2)

    result = annotate_task("t-nokey")
    assert result["review"] == 2
    assert result["auto"] == 0
    assert all(s["status"] == "review" for s in load_samples("t-nokey"))


def test_review_queue_confirm_and_skip(client, tmp_settings):
    from opencam.training.label import annotate_task

    _seed_task("t-review", n_frames=2)

    def fake_label(_image_bytes, _definition, _cfg):
        return "满溢", 0.2, "不确定"
    annotate_task("t-review", label_fn=fake_label)

    resp = client.get("/training/tasks/t-review/review")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["remaining"] == 2
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert item["suggested_label"] == "满溢"
    assert set(item["classes"]) == {"空/正常", "将满", "满溢"}
    sample_id = item["id"]

    resp = client.post(
        f"/training/tasks/t-review/review/{sample_id}",
        json={"action": "confirm", "label": "空/正常"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["label"] == "空/正常"
    dataset = task_dir("t-review") / "dataset" / "空/正常"
    assert list(dataset.glob("*.jpg"))

    other_id = body["items"][1]["id"]
    resp = client.post(
        f"/training/tasks/t-review/review/{other_id}",
        json={"action": "skip"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"

    left = client.get("/training/tasks/t-review/review").json()
    assert left["remaining"] == 0
    assert left["items"] == []


def test_review_unknown_task_is_404(client, tmp_settings):
    assert client.get("/training/tasks/missing/review").status_code == 404


def test_review_confirm_rejects_unknown_class(client, tmp_settings):
    from opencam.training.label import annotate_task

    _seed_task("t-bad", n_frames=1)

    def fake_label(_image_bytes, _definition, _cfg):
        return "满溢", 0.1, "x"
    annotate_task("t-bad", label_fn=fake_label)
    sample_id = load_samples("t-bad")[0]["id"]

    resp = client.post(
        f"/training/tasks/t-bad/review/{sample_id}",
        json={"action": "confirm", "label": "不是类别"},
    )
    assert resp.status_code == 400


def test_annotate_endpoint_uses_injected_style_pipeline(client, tmp_settings):
    """POST /annotate 无 VLM key 时全部进入确认队列。"""
    _seed_task("t-api", n_frames=1)
    resp = client.post("/training/tasks/t-api/annotate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review"] == 1
    assert body["auto"] == 0
