"""训练产物与数据集的落盘路径管理。

目录结构（全部在 data/training/<task_id>/ 下，不进 git）：

    frames/            抽帧并按固定区域裁剪后的样本图
    dataset/
      train/<类别>/    训练集（ultralytics 分类目录格式）
      val/<类别>/      验证集
    models/v<N>/       第 N 版训练产物（best.pt 等）
    report.json        最近一次评估报告
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import settings


def task_dir(task_id: int) -> Path:
    """任务数据根目录。"""
    return settings.data_dir / "training" / str(task_id)


def frames_dir(task_id: int) -> Path:
    return task_dir(task_id) / "frames"


def dataset_dir(task_id: int) -> Path:
    return task_dir(task_id) / "dataset"


def model_dir(task_id: int, version: int) -> Path:
    return task_dir(task_id) / "models" / f"v{version}"


def report_path(task_id: int) -> Path:
    return task_dir(task_id) / "report.json"


def ensure_task_dirs(task_id: int) -> Path:
    """创建任务目录骨架，返回根目录。"""
    root = task_dir(task_id)
    frames_dir(task_id).mkdir(parents=True, exist_ok=True)
    return root


def safe_class_name(name: str) -> str:
    """类别名转为安全的目录名（类别可能是中文/含空格）。"""
    cleaned = re.sub(r"[^\w一-鿿-]+", "_", name.strip())
    return cleaned or "class"


def next_version(task_id: int) -> int:
    """扫描 models/v<N>/ 目录得出下一个版本号。"""
    models_root = task_dir(task_id) / "models"
    existing = [int(m.group(1)) for d in models_root.glob("v*")
                if (m := re.fullmatch(r"v(\d+)", d.name))]
    return max(existing, default=0) + 1
