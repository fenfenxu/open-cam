"""自动标注流水线：裁剪 → VLM 打标 → 按置信度分流。

高置信直接入 dataset/<class>/；低置信 / 失败 / 无 key 进人工确认队列。
label_fn 可注入，测试不走网络。
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import cv2
import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    SAMPLE_ACCEPTED,
    SAMPLE_PENDING,
    SAMPLE_SKIPPED,
    TASK_LABELED,
    TASK_LABELING,
    TASK_REVIEW,
    TrainingSample,
    TrainingTask,
)
from .crop import crop_frame
from .storage import (
    crops_dir,
    dataset_class_dir,
    ensure_layout,
    list_frame_paths,
)
from .vlm_label import (
    LabelVlmConfig,
    definition_fields,
    label_crop,
    resolve_label_vlm,
)

logger = logging.getLogger(__name__)

LabelFn = Callable[[str, TrainingTask, LabelVlmConfig], tuple[Optional[str], float]]


def sample_stats(session: Session, task_id: int) -> dict[str, int]:
    rows = session.query(TrainingSample).filter_by(task_id=task_id).all()
    stats = {"accepted": 0, "pending_review": 0, "skipped": 0, "total": len(rows)}
    for row in rows:
        if row.status == SAMPLE_ACCEPTED:
            stats["accepted"] += 1
        elif row.status == SAMPLE_SKIPPED:
            stats["skipped"] += 1
        else:
            stats["pending_review"] += 1
    return stats


def copy_into_dataset(task_id: int, crop_path: str, label: str) -> None:
    dest_dir = dataset_class_dir(task_id, label)
    src = Path(crop_path)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)


def apply_human_decision(session: Session, sample: TrainingSample, *,
                         label: Optional[str], skip: bool,
                         classes: list[str]) -> TrainingSample:
    """用户点类别或跳过。"""
    if skip:
        sample.status = SAMPLE_SKIPPED
        sample.source = "human"
        sample.label = None
        session.commit()
        session.refresh(sample)
        return sample
    if not label or (classes and label not in classes):
        raise ValueError("必须从任务封闭类别中选一个，或 skip")
    sample.label = label
    sample.status = SAMPLE_ACCEPTED
    sample.source = "human"
    copy_into_dataset(sample.task_id, sample.crop_path, label)
    session.commit()
    session.refresh(sample)
    return sample


def run_labeling(session: Session, task: TrainingTask, *,
                 label_fn: Optional[LabelFn] = None) -> dict[str, int]:
    """对任务 frames/ 全量打标。可重复跑：先清掉旧样本行（不删用户已确认的）。"""
    frames = list_frame_paths(task.id)
    if not frames:
        raise FileNotFoundError("没有抽帧，请先将图片放到 data/training/<id>/frames/")

    ensure_layout(task.id)
    cfg = resolve_label_vlm(task)
    object_name, property_name, classes = definition_fields(task.definition or {})
    if not classes:
        raise ValueError("任务定义缺少封闭类别 classes")

    task.status = TASK_LABELING
    session.commit()

    # 重跑时去掉尚未人工确认的自动样本，保留 human 已确认/跳过
    session.query(TrainingSample).filter(
        TrainingSample.task_id == task.id,
        TrainingSample.source != "human",
    ).delete()
    session.commit()

    fn = label_fn or _default_label_fn(object_name, property_name, classes)
    crop_root = crops_dir(task.id)

    for frame_path in frames:
        frame = cv2.imread(str(frame_path))
        if frame is None:
            logger.warning("读帧失败，跳过 %s", frame_path)
            continue
        crop = crop_frame(frame, task.region)
        crop_path = crop_root / frame_path.name
        cv2.imwrite(str(crop_path), crop)

        predicted, confidence = None, 0.0
        try:
            predicted, confidence = fn(str(crop_path), task, cfg)
        except Exception as exc:  # noqa: BLE001 单张失败进人工队列
            logger.warning("VLM 打标失败 %s: %s", frame_path.name, exc)

        in_classes = bool(predicted) and predicted in classes
        high = in_classes and confidence >= cfg.confidence_threshold
        sample = TrainingSample(
            task_id=task.id,
            frame_path=str(frame_path),
            crop_path=str(crop_path),
            predicted_label=predicted,
            confidence=confidence,
            label=predicted if high else None,
            status=SAMPLE_ACCEPTED if high else SAMPLE_PENDING,
            source="auto",
        )
        session.add(sample)
        session.flush()
        if high and predicted:
            # 用样本 id 避免重名覆盖
            dest_name = f"{sample.id}_{frame_path.name}"
            dest = dataset_class_dir(task.id, predicted) / dest_name
            shutil.copy2(crop_path, dest)
            sample.crop_path = str(crop_path)
        session.commit()

    stats = sample_stats(session, task.id)
    task.status = TASK_REVIEW if stats["pending_review"] else TASK_LABELED
    session.commit()
    return stats


def _default_label_fn(object_name: str, property_name: str,
                      classes: list[str]) -> LabelFn:
    def _call(image_path: str, task: TrainingTask, cfg: LabelVlmConfig
              ) -> tuple[Optional[str], float]:
        if not settings.vlm_api_key:
            # 无 key：全部进人工队列，不发起站外请求
            return None, 0.0
        headers = {"Authorization": f"Bearer {settings.vlm_api_key}"}
        with httpx.Client(headers=headers) as client:
            return label_crop(
                client, image_path,
                object_name=object_name,
                property_name=property_name,
                classes=classes,
                cfg=cfg,
            )
    return _call
