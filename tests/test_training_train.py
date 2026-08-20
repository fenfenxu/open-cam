"""训练执行与评估报告。

测试绝不触碰真实 YOLO 权重：train_fn / predict_fn 注入假实现；
API 侧 monkeypatch train_and_evaluate，后台线程照常跑。
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import opencam.training.train as train_module
from opencam.training.evaluate import (
    compute_metrics,
    judge_pass,
    resolve_alert_class,
)
from opencam.training.storage import save_definition, save_samples, task_dir
from opencam.training.train import (
    latest_run_report,
    prepare_split,
    sanitize_class_name,
    train_and_evaluate,
    validate_trainable,
)


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _solid_frame(w=32, h=24, color=(0, 0, 255)) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _seed_dataset(task_id: str, spec: dict[str, int], confirmed: int = 2):
    """写入定义 + dataset/<类别>/ 图片 + samples.json（每类末尾 confirmed 张为确认样本）。"""
    classes = list(spec)
    save_definition(task_id, {
        "object": "垃圾桶",
        "property": "满溢状态",
        "classes": classes,
        "rule": {"type": "state_alert", "trigger": f"{classes[-1]} 持续 5 分钟"},
        "metrics": {"accuracy": 0.9, "recall": 0.85, "false_alarm_per_day": 2},
    })
    samples = []
    n = 0
    for cls, auto_count in spec.items():
        for i in range(auto_count + confirmed):
            n += 1
            sid = f"s{n:04d}"
            dest = task_dir(task_id) / "dataset" / cls  # 类别含 "/" 时是嵌套目录
            dest.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(dest / f"{sid}.jpg"), _solid_frame())
            samples.append({
                "id": sid,
                "label": cls,
                "status": "confirmed" if i >= auto_count else "auto",
                "crop": f"crops/{sid}.jpg",
            })
    save_samples(task_id, samples)


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/training/tasks/{task_id}/train").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("训练超时未结束")


def test_sanitize_class_name_replaces_separators():
    assert sanitize_class_name("空/正常") == "空_正常"
    assert sanitize_class_name("满溢") == "满溢"
    assert sanitize_class_name(" ") == "unnamed"


def test_resolve_alert_class_priority():
    definition = {
        "classes": ["空/正常", "将满", "满溢"],
        "rule": {"trigger": "满溢 持续 5 分钟"},
    }
    assert resolve_alert_class(definition) == "满溢"
    assert resolve_alert_class({**definition, "alert_class": "将满"}) == "将满"
    # trigger 不含类别名时兜底最后一类
    assert resolve_alert_class({
        "classes": ["合规", "未穿"],
        "rule": {"trigger": "持续 10 秒"},
    }) == "未穿"


def test_compute_metrics_three_indicators():
    classes = ["空/正常", "满溢"]
    pairs = (
        [("空/正常", "空/正常")] * 8
        + [("空/正常", "满溢")] * 2
        + [("满溢", "满溢")] * 9
        + [("满溢", "空/正常")]
    )
    m = compute_metrics(pairs, classes, "满溢", checks_per_day=100)
    assert m["total"] == 20
    assert m["accuracy"] == pytest.approx(17 / 20)
    assert m["recall"] == pytest.approx(9 / 10)
    # 非告警 10 张中 2 张误判为告警 → 0.2 × 100 次/天
    assert m["false_alarm_per_day"] == pytest.approx(20.0)
    assert m["per_class"]["满溢"]["support"] == 10

    passed, reasons = judge_pass(
        m, {"accuracy": 0.8, "recall": 0.85, "false_alarm_per_day": 30})
    assert passed
    assert reasons == []

    passed, reasons = judge_pass(
        m, {"accuracy": 0.9, "recall": 0.85, "false_alarm_per_day": 2})
    assert not passed
    assert any("准确率" in r for r in reasons)
    assert any("误报" in r for r in reasons)


def test_validate_requires_confirmed_samples(tmp_settings):
    _seed_dataset("t-noconf", {"正常": 3, "满溢": 3}, confirmed=0)
    with pytest.raises(ValueError, match="人工确认"):
        validate_trainable("t-noconf")


def test_validate_requires_two_classes(tmp_settings):
    _seed_dataset("t-oneclass", {"满溢": 3}, confirmed=1)
    with pytest.raises(ValueError, match="2 个类别"):
        validate_trainable("t-oneclass")


def test_prepare_split_puts_confirmed_in_val(tmp_settings):
    _seed_dataset("t-split", {"空/正常": 6, "满溢": 6}, confirmed=2)
    prep = prepare_split("t-split", val_ratio=0.2)
    val = [m for m in prep["manifest"] if m["subset"] == "val"]
    train = [m for m in prep["manifest"] if m["subset"] == "train"]
    # 确认样本全部进评估集，训练侧只有自动标注
    assert prep["confirmed_in_val"] == 4
    assert all(m["status"] == "auto" for m in train)
    # 每类 val = round(6×0.2)=1 自动 + 2 确认
    assert len(val) == 6
    # 类别名含 "/" 清洗为一级目录（YOLO 分类要求）
    split_train = task_dir("t-split") / "split" / "train"
    assert {p.parent.name for p in split_train.rglob("*.jpg")} == {
        "空_正常", "满溢"}
    assert (task_dir("t-split") / "split" / "manifest.json").is_file()


def _fake_train(split_dir, run_dir, params):
    best = run_dir / "best.pt"
    best.write_bytes(b"fake-weights")
    return best


def _perfect_predict(model_path, images, name_map):
    # split 目录的父目录名即清洗后的真实类别
    return [name_map[p.parent.name] for p in images]


def test_train_and_evaluate_pass_with_fakes(tmp_settings):
    _seed_dataset("t-run", {"空/正常": 6, "满溢": 6}, confirmed=2)
    report = train_and_evaluate(
        "t-run", {"epochs": 1},
        train_fn=_fake_train, predict_fn=_perfect_predict)

    assert report["passed"] is True
    assert report["metrics"]["accuracy"] == 1.0
    assert report["metrics"]["recall"] == 1.0
    assert report["metrics"]["false_alarm_per_day"] == 0.0
    assert report["eval"]["confirmed"] == 4
    assert report["alert_class"] == "满溢"
    assert "达标" in report["conclusion"]

    run_dir = task_dir("t-run") / "models" / report["run_id"]
    assert (run_dir / "best.pt").is_file()
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "结论" in md and "准确率" in md and "典型样本" in md
    assert (run_dir / "report.json").is_file()
    # 服务重启后能从磁盘回填最近一次报告
    assert latest_run_report("t-run")["run_id"] == report["run_id"]


def test_train_and_evaluate_fail_lists_wrong_examples(tmp_settings):
    _seed_dataset("t-wrong", {"空/正常": 6, "满溢": 6}, confirmed=2)

    def flip_first(model_path, images, name_map):
        preds = _perfect_predict(model_path, images, name_map)
        preds[0] = "满溢" if preds[0] != "满溢" else "空/正常"
        return preds

    report = train_and_evaluate(
        "t-wrong", {}, train_fn=_fake_train, predict_fn=flip_first)
    assert report["passed"] is False
    assert report["metrics"]["accuracy"] == pytest.approx(5 / 6)
    assert len(report["examples"]["wrong"]) == 1
    wrong = report["examples"]["wrong"][0]
    assert wrong["true"] != wrong["pred"]
    assert report["suggestions"]


def test_train_endpoint_runs_background_job(client, tmp_settings, monkeypatch):
    _seed_dataset("t-api-train", {"正常": 4, "满溢": 4}, confirmed=1)

    def fake_job(task_id, params, run_id=None):
        assert params["epochs"] == 3
        return {"task_id": task_id, "run_id": run_id, "passed": True}

    monkeypatch.setattr(train_module, "train_and_evaluate", fake_job)
    resp = client.post("/api/training/tasks/t-api-train/train", json={"epochs": 3})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"
    assert resp.json()["run_id"]

    final = _wait_done(client, "t-api-train")
    assert final["status"] == "done"
    assert final["result"]["passed"] is True


def test_train_conflict_while_running(client, tmp_settings, monkeypatch):
    _seed_dataset("t-conflict", {"正常": 4, "满溢": 4}, confirmed=1)
    gate = threading.Event()

    def slow_job(task_id, params, run_id=None):
        gate.wait(5)
        return {"task_id": task_id, "run_id": run_id}

    monkeypatch.setattr(train_module, "train_and_evaluate", slow_job)
    assert client.post("/api/training/tasks/t-conflict/train").status_code == 202
    resp = client.post("/api/training/tasks/t-conflict/train")
    assert resp.status_code == 409
    gate.set()
    assert _wait_done(client, "t-conflict")["status"] == "done"


def test_train_failure_recorded(client, tmp_settings, monkeypatch):
    _seed_dataset("t-fail", {"正常": 4, "满溢": 4}, confirmed=1)

    def boom(task_id, params, run_id=None):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(train_module, "train_and_evaluate", boom)
    assert client.post("/api/training/tasks/t-fail/train").status_code == 202
    final = _wait_done(client, "t-fail")
    assert final["status"] == "failed"
    assert "磁盘满了" in final["error"]


def test_train_unknown_task_404(client, tmp_settings):
    assert client.post("/api/training/tasks/missing/train").status_code == 404
    assert client.get("/api/training/tasks/missing/train").status_code == 404


def test_train_without_dataset_400(client, tmp_settings):
    save_definition("t-empty", {
        "object": "垃圾桶", "property": "满溢状态",
        "classes": ["正常", "满溢"],
    })
    resp = client.post("/api/training/tasks/t-empty/train")
    assert resp.status_code == 400
    assert "数据集" in resp.json()["detail"]


def test_train_status_idle_before_any_run(client, tmp_settings):
    save_definition("t-idle", {
        "object": "垃圾桶", "property": "满溢状态",
        "classes": ["正常", "满溢"],
    })
    body = client.get("/api/training/tasks/t-idle/train").json()
    assert body["status"] == "idle"
    assert body["result"] is None
