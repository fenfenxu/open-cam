"""训练产物路径：全部落在 data/training/<task_id>/，不进 git。"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import settings

_FRAME_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def task_dir(task_id: int) -> Path:
    return settings.data_dir / "training" / str(task_id)


def frames_dir(task_id: int) -> Path:
    return task_dir(task_id) / "frames"


def crops_dir(task_id: int) -> Path:
    return task_dir(task_id) / "crops"


def dataset_dir(task_id: int) -> Path:
    return task_dir(task_id) / "dataset"


def ensure_layout(task_id: int) -> None:
    """建齐 frames / crops / dataset 目录。"""
    for path in (frames_dir(task_id), crops_dir(task_id), dataset_dir(task_id)):
        path.mkdir(parents=True, exist_ok=True)


def list_frame_paths(task_id: int) -> list[Path]:
    root = frames_dir(task_id)
    if not root.exists():
        return []
    files = [p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in _FRAME_EXTS]
    return sorted(files)


def class_dirname(label: str) -> str:
    """类别名可能含 '/'，不能当目录名；换成全角或下划线。"""
    cleaned = re.sub(r"[\\/]+", "_", label).strip() or "unknown"
    return cleaned[:80]


def dataset_class_dir(task_id: int, label: str) -> Path:
    path = dataset_dir(task_id) / class_dirname(label)
    path.mkdir(parents=True, exist_ok=True)
    return path
