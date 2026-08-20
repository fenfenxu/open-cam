"""模型资产生命周期的本机初始化逻辑。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .config import settings
from .models import (
    MODEL_KIND_DETECTION,
    MODEL_SOURCE_BUILTIN,
    ModelAsset,
)


def ensure_builtin_assets(session: Session) -> None:
    """幂等登记当前系统自带的基础模型，不覆盖用户编辑过的描述。"""
    task_key = "person_detection"
    exists = (session.query(ModelAsset)
              .filter_by(source_type=MODEL_SOURCE_BUILTIN, task_key=task_key)
              .first())
    if exists is not None:
        return
    session.add(ModelAsset(
        name="YOLOv8 Nano（系统内置）",
        description="系统默认目标检测模型，适合人员、车辆等通用目标检测。",
        source_type=MODEL_SOURCE_BUILTIN,
        model_kind=MODEL_KIND_DETECTION,
        task_key=task_key,
        metadata_json={"artifact_path": settings.yolo_model},
    ))
    session.commit()
