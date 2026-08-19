"""训练任务落盘约定：data/training/<task_id>/，不进 git。

与 CAM-3 抽帧骨架共用同一目录结构：
- definition.json  任务定义（对象/属性/封闭类别/区域/任务级 VLM）
- frames/          抽帧原图
- crops/           固定区域裁剪
- samples.json     标注状态（auto/review/confirmed/skipped）
- dataset/<类别>/  已入训练集的裁剪图
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import settings

_TASK_ID_RE = re.compile(r"^[\w.-]+$")


def ensure_task_id(task_id: str) -> str:
    """拒绝路径穿越；只允许字母数字、点、下划线、连字符。"""
    if not task_id or not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"非法任务 id: {task_id!r}")
    return task_id


def task_dir(task_id: str) -> Path:
    return settings.data_dir / "training" / ensure_task_id(task_id)


def task_exists(task_id: str) -> bool:
    return (task_dir(task_id) / "definition.json").is_file()


def list_task_ids() -> list[str]:
    """扫描 data/training/ 下有 definition.json 或 draft.json 的任务。"""
    root = settings.data_dir / "training"
    if not root.is_dir():
        return []
    ids: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        try:
            ensure_task_id(path.name)
        except ValueError:
            continue
        if (path / "definition.json").is_file() or (path / "draft.json").is_file():
            ids.append(path.name)
    return ids


def save_definition(task_id: str, definition: dict[str, Any]) -> Path:
    root = task_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "frames").mkdir(exist_ok=True)
    (root / "crops").mkdir(exist_ok=True)
    (root / "dataset").mkdir(exist_ok=True)
    path = root / "definition.json"
    path.write_text(json.dumps(definition, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_definition(task_id: str) -> dict[str, Any]:
    path = task_dir(task_id) / "definition.json"
    if not path.is_file():
        raise FileNotFoundError(task_id)
    return json.loads(path.read_text(encoding="utf-8"))


def write_frame(task_id: str, filename: str, frame: np.ndarray) -> Path:
    """把一张抽帧写成 JPEG，供标注流水线读取。"""
    root = task_dir(task_id)
    (root / "frames").mkdir(parents=True, exist_ok=True)
    dest = root / "frames" / Path(filename).name
    if not cv2.imwrite(str(dest), frame):
        raise RuntimeError(f"写抽帧失败: {dest}")
    return dest


def list_frames(task_id: str) -> list[Path]:
    frames = task_dir(task_id) / "frames"
    if not frames.is_dir():
        return []
    return sorted(p for p in frames.iterdir()
                  if p.suffix.lower() in {".jpg", ".jpeg", ".png"})


def load_samples(task_id: str) -> list[dict[str, Any]]:
    path = task_dir(task_id) / "samples.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("samples", [])


def save_samples(task_id: str, samples: list[dict[str, Any]]) -> None:
    path = task_dir(task_id) / "samples.json"
    path.write_text(
        json.dumps({"samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
