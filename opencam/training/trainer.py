"""训练执行：组织数据集 → YOLO 分类微调 → 验证集评估 → 产出模型版本。

- 数据集：已确认样本（SAMPLE_AUTO / SAMPLE_CONFIRMED）按 8:2 切分
  train/val，ultralytics 分类目录格式（train/<类别>/*.jpg）。
- 真实模式从预训练 yolov8n-cls 微调；mock 模式（OPENCAM_DETECTOR=mock，
  无模型环境/CI）跳过真实训练，产出占位权重 + 确定性伪指标，
  保证全流程可测。mock 指标固定"达标"，仅验证链路不代表真实质量。
- TrainingRunner：daemon 线程，同一时刻只训一个任务（本地机器一次
  一个微调已足够）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

import cv2

from ..config import settings
from ..db import get_session
from ..models import (MODEL_TRAINED, SAMPLE_AUTO, SAMPLE_CONFIRMED,
                      TASK_FAILED, TASK_TRAINED, TASK_TRAINING,
                      TrainedModel, TrainingSample, TrainingTask)
from . import store
from .report import build_report

logger = logging.getLogger(__name__)

# 验证集比例
VAL_RATIO = 0.2

# mock 模式的固定伪指标（确定性，仅用于无模型环境验证链路）
MOCK_METRICS = {"accuracy": 0.93, "recall": 0.88, "false_positive_rate": 0.04}


def use_mock() -> bool:
    """是否走 mock 训练（沿用检测器的 mock 开关，环境变量优先）。"""
    return os.environ.get(
        "OPENCAM_DETECTOR", settings.detector).lower() == "mock"


def prepare_dataset(task_id: int) -> dict[str, Any]:
    """把已确认样本组织成 ultralytics 分类数据集，返回统计。

    每类至少 2 张才能切出验证集；某类只有 1 张时全部进训练集。
    """
    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        if task is None:
            raise ValueError(f"训练任务不存在: {task_id}")
        samples = session.query(TrainingSample).filter(
            TrainingSample.task_id == task_id,
            TrainingSample.status.in_([SAMPLE_AUTO, SAMPLE_CONFIRMED]),
        ).order_by(TrainingSample.id).all()
        classes = list(task.classes)
        items = [(s.id, s.image_path, s.final_label) for s in samples
                 if s.final_label in classes]
    finally:
        session.close()

    by_class: dict[str, list[tuple[int, str]]] = {c: [] for c in classes}
    for sample_id, image_path, label in items:
        by_class[label].append((sample_id, image_path))

    for cls, cls_items in by_class.items():
        if len(cls_items) < 2:
            raise ValueError(
                f"类别「{cls}」只有 {len(cls_items)} 张已确认样本，"
                f"每类至少需要 2 张才能训练")

    root = store.dataset_dir(task_id)
    if root.exists():
        shutil.rmtree(root)
    val_map: list[tuple[int, str, Path]] = []  # (sample_id, label, val_path)
    counts = {"train": 0, "val": 0}
    for cls, cls_items in by_class.items():
        safe = store.safe_class_name(cls)
        n_val = max(1, int(round(len(cls_items) * VAL_RATIO)))
        val_set = cls_items[:n_val]
        train_set = cls_items[n_val:]
        for split, group in (("train", train_set), ("val", val_set)):
            dest_dir = root / split / safe
            dest_dir.mkdir(parents=True, exist_ok=True)
            for sample_id, image_path in group:
                dest = dest_dir / f"sample_{sample_id}.jpg"
                shutil.copy2(image_path, dest)
                counts[split] += 1
                if split == "val":
                    val_map.append((sample_id, cls, dest))
    logger.info("任务 %d 数据集就绪: train=%d val=%d",
                task_id, counts["train"], counts["val"])
    return {"counts": counts, "val_map": val_map}


def train(task_id: int, epochs: int = 20,
          stop: Optional[threading.Event] = None) -> TrainedModel:
    """执行训练并评估，产出 TrainedModel 行。失败时任务置 failed。"""
    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        if task is None:
            raise ValueError(f"训练任务不存在: {task_id}")
        task.status = TASK_TRAINING
        task.error = None
        task.updated_at = time.time()
        session.commit()
        session.refresh(task)
        snapshot = {  # 取出纯数据，后续长训练不持有会话
            "id": task.id, "object_name": task.object_name,
            "property_name": task.property_name, "classes": list(task.classes),
            "rule": dict(task.rule or {}), "metrics": dict(task.metrics or {}),
        }
    finally:
        session.close()

    try:
        prepared = prepare_dataset(task_id)
        version = store.next_version(task_id)
        model_path = store.model_dir(task_id, version)
        model_path.mkdir(parents=True, exist_ok=True)
        if use_mock():
            weights, metrics, val_samples = _mock_train(
                task_id, snapshot, model_path, prepared)
        else:
            weights, metrics, val_samples = _real_train(
                task_id, snapshot, model_path, prepared, epochs)
        report = build_report(_TaskView(snapshot), metrics,
                              prepared["counts"], val_samples)
        store.report_path(task_id).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")

        session = get_session()
        try:
            model = TrainedModel(
                task_id=task_id, version=version, path=str(weights),
                metrics=metrics, report=report["conclusion"])
            session.add(model)
            task = session.get(TrainingTask, task_id)
            task.status = TASK_TRAINED
            task.updated_at = time.time()
            session.commit()
            session.refresh(model)
            session.expunge(model)
            return model
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 训练失败要落状态，便于前端展示
        logger.exception("任务 %d 训练失败", task_id)
        session = get_session()
        try:
            task = session.get(TrainingTask, task_id)
            if task is not None:
                task.status = TASK_FAILED
                task.error = str(exc)[:500]
                task.updated_at = time.time()
                session.commit()
        finally:
            session.close()
        raise


class _TaskView:
    """build_report 需要的最小任务视图（避免长训练持有 ORM 对象）。"""

    def __init__(self, data: dict[str, Any]):
        self.__dict__.update(data)
        self.metrics = data["metrics"]
        self.rule = data["rule"]


def _mock_train(task_id: int, task: dict[str, Any], model_path: Path,
                prepared: dict[str, Any]):
    """无模型环境：写占位权重，返回固定伪指标。"""
    weights = model_path / "best.pt"
    weights.write_text("mock weights (OPENCAM_DETECTOR=mock)\n",
                       encoding="utf-8")
    val_samples = [{"sample_id": sid, "true": label, "pred": label}
                   for sid, label, _ in prepared["val_map"]]
    logger.info("任务 %d mock 训练完成（占位权重）: %s", task_id, weights)
    return weights, dict(MOCK_METRICS), val_samples


def _real_train(task_id: int, task: dict[str, Any], model_path: Path,
                prepared: dict[str, Any], epochs: int):
    """真实微调：yolov8n-cls 分类训练 + 验证集推理评估。"""
    from ultralytics import YOLO  # 懒加载：训练时才拉 torch/权重

    from ..hardware import resolve_device
    from .classifier import YoloStateClassifier

    dataset = store.dataset_dir(task_id)
    model = YOLO("yolov8n-cls.pt")
    model.train(data=str(dataset), epochs=epochs, imgsz=64,
                device=resolve_device(settings.device),
                project=str(model_path), name="run", exist_ok=True,
                verbose=False)
    src = model_path / "run" / "weights" / "best.pt"
    weights = model_path / "best.pt"
    shutil.copy2(src, weights)

    # 验证集逐张推理算指标
    clf = YoloStateClassifier(str(weights))
    tp = fn = fp = correct = total = 0
    trigger = task["rule"].get("trigger_class", "")
    val_samples: list[dict[str, Any]] = []
    for sample_id, label, img_path in prepared["val_map"]:
        image = cv2.imread(str(img_path))
        if image is None:
            continue
        pred, conf = clf.predict(image)
        total += 1
        correct += int(pred == label)
        if label == trigger:
            tp += int(pred == trigger)
            fn += int(pred != trigger)
        elif pred == trigger:
            fp += 1
        val_samples.append({"sample_id": sample_id, "true": label,
                            "pred": pred, "confidence": round(conf, 3)})
    metrics = {
        "accuracy": round(correct / total, 4) if total else 0.0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
        "false_positive_rate": round(
            fp / (total - tp - fn), 4) if (total - tp - fn) else 0.0,
    }
    return weights, metrics, val_samples


class TrainingRunner:
    """训练后台线程（daemon + stop 信号），同一时刻只训一个任务。"""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.active_task_id: Optional[int] = None

    def start(self, task_id: int, epochs: int = 20) -> bool:
        """启动训练；已有任务在训则返回 False。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self.active_task_id = task_id
            self._thread = threading.Thread(
                target=self._run, args=(task_id, epochs),
                name=f"training-{task_id}", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self, task_id: Optional[int] = None) -> bool:
        alive = bool(self._thread and self._thread.is_alive())
        if task_id is None:
            return alive
        return alive and self.active_task_id == task_id

    def _run(self, task_id: int, epochs: int) -> None:
        try:
            train(task_id, epochs=epochs, stop=self._stop)
        except Exception:  # noqa: BLE001 失败已在 train 内落库
            logger.exception("任务 %d 训练线程异常", task_id)
        finally:
            self.active_task_id = None


# 全局单例
training_runner = TrainingRunner()
