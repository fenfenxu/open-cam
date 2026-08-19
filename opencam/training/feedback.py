"""事件误报/漏报飞轮：快照裁剪后写入对应训练任务数据集。

误报 → 真实类别取非告警类（默认第一类或显式 label）；
漏报 → 真实类别取告警类。样本 source=feedback，直接 confirmed 入集。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import cv2

from .crop import crop_polygon
from .evaluate import resolve_alert_class
from .label import STATUS_CONFIRMED, copy_to_dataset
from .storage import load_definition, load_samples, save_samples, task_dir

KIND_FALSE_ALARM = "false_alarm"
KIND_MISS = "miss"
VALID_KINDS = (KIND_FALSE_ALARM, KIND_MISS)


class FeedbackError(ValueError):
    """可映射为 HTTP 4xx。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def default_label_for(kind: str, definition: dict[str, Any]) -> str:
    classes = list(definition.get("classes") or [])
    if not classes:
        raise FeedbackError("任务没有封闭类别，无法归入数据集")
    alert = resolve_alert_class(definition)
    if kind == KIND_MISS:
        return alert or classes[-1]
    # 误报：取第一个非告警类
    for cls in classes:
        if cls != alert:
            return str(cls)
    return str(classes[0])


def ingest_event_feedback(
    task_id: str,
    event_id: int,
    kind: str,
    snapshot_path: Optional[str],
    label: Optional[str] = None,
) -> dict[str, Any]:
    """把事件快照写入任务 crops/ + dataset/<label>/。同一事件+kind 幂等。"""
    if kind not in VALID_KINDS:
        raise FeedbackError("kind 只能是 false_alarm 或 miss")
    try:
        definition = load_definition(task_id)
    except FileNotFoundError as exc:
        raise FeedbackError("训练任务不存在", 404) from exc
    classes = list(definition.get("classes") or [])
    chosen = label or default_label_for(kind, definition)
    if chosen not in classes:
        raise FeedbackError("类别不在任务封闭集合中")
    if not snapshot_path or not Path(snapshot_path).is_file():
        raise FeedbackError("事件没有可用快照", 404)

    samples = load_samples(task_id)
    existing = next(
        (s for s in samples
         if s.get("event_id") == event_id and s.get("kind") == kind),
        None,
    )
    if existing:
        return {**existing, "already": True}

    sample_id = f"fb-e{event_id}-{kind}"
    frame = cv2.imread(snapshot_path)
    if frame is None:
        raise FeedbackError("无法读取事件快照")

    region = definition.get("region") or []
    try:
        crop = crop_polygon(frame, region) if region else frame
    except ValueError:
        crop = frame

    crops_dir = task_dir(task_id) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    crop_rel = f"crops/{sample_id}.jpg"
    if not cv2.imwrite(str(task_dir(task_id) / crop_rel), crop):
        raise FeedbackError("写入裁剪图失败")

    sample: dict[str, Any] = {
        "id": sample_id,
        "event_id": event_id,
        "kind": kind,
        "crop": crop_rel,
        "status": STATUS_CONFIRMED,
        "label": chosen,
        "confidence": 1.0,
        "reason": "事件页反馈",
        "source": "feedback",
    }
    copy_to_dataset(task_id, sample)
    samples.append(sample)
    save_samples(task_id, samples)
    return {**sample, "already": False}
