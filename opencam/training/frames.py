"""抽帧：从摄像头实时流或视频文件采样，按固定区域裁剪，感知哈希去重。

- 文件源：OpenCV 顺序读帧，按 interval_s 采样，全片扫完为止。
- 摄像头源：须处于 running，从环形帧缓冲按 interval_s 采样 duration_s。
- 裁剪：polygon（0-1 相对坐标）取外接矩形裁剪——固定区域场景下
  框由用户画一次，训练目标退化为区域小图的分类问题。
- 去重：average hash 汉明距离 < 阈值视为重复帧，直接丢弃。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .store import ensure_task_dirs, frames_dir

logger = logging.getLogger(__name__)

# 感知哈希去重阈值（64 位 ahash 的汉明距离）
_DUP_HAMMING = 5


def polygon_crop_rect(polygon: list[list[float]], width: int,
                      height: int) -> tuple[int, int, int, int]:
    """0-1 相对坐标多边形 → 像素外接矩形 (x1, y1, x2, y2)，裁剪到画面内。"""
    xs = [min(max(p[0], 0.0), 1.0) * width for p in polygon]
    ys = [min(max(p[1], 0.0), 1.0) * height for p in polygon]
    x1, x2 = int(min(xs)), int(max(xs))
    y1, y2 = int(min(ys)), int(max(ys))
    # 至少 1 像素，避免空裁剪
    return x1, y1, max(x2, x1 + 1), max(y2, y1 + 1)


def crop_frame(frame: np.ndarray,
               polygon: list[list[float]]) -> np.ndarray:
    """按固定区域裁剪帧；polygon 为空时返回原帧。"""
    if not polygon:
        return frame
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = polygon_crop_rect(polygon, w, h)
    return frame[y1:y2, x1:x2]


def _ahash(frame: np.ndarray) -> int:
    """8x8 平均哈希，返回 64 位整数。"""
    small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (8, 8))
    mean = small.mean()
    bits = (small > mean).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _save_frame(frame: np.ndarray, dest: Path, seen_hashes: list[int]) -> bool:
    """去重后写 JPEG；重复帧返回 False。"""
    h = _ahash(frame)
    if any(_hamming(h, prev) < _DUP_HAMMING for prev in seen_hashes):
        return False
    cv2.imwrite(str(dest), frame)
    seen_hashes.append(h)
    return True


def extract_from_file(task_id: int, video_path: str,
                      polygon: list[list[float]], interval_s: float = 2.0,
                      max_frames: int = 100) -> list[Path]:
    """从视频文件顺序抽帧。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"无法打开视频文件: {video_path}")
    ensure_task_dirs(task_id)
    out_dir = frames_dir(task_id)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps * interval_s)))
    saved: list[Path] = []
    seen: list[int] = []
    idx = 0
    try:
        while len(saved) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                crop = crop_frame(frame, polygon)
                dest = out_dir / f"frame_{len(saved):04d}.jpg"
                if _save_frame(crop, dest, seen):
                    saved.append(dest)
            idx += 1
    finally:
        cap.release()
    logger.info("任务 %d 从文件抽帧 %d 张（去重后）: %s",
                task_id, len(saved), video_path)
    return saved


def extract_from_camera(task_id: int, camera_id: int,
                        polygon: list[list[float]], interval_s: float = 2.0,
                        max_frames: int = 100,
                        duration_s: float = 60.0) -> list[Path]:
    """从运行中的摄像头实时采样（依赖 streams 的环形帧缓冲）。"""
    from ..streams.manager import camera_manager

    ensure_task_dirs(task_id)
    out_dir = frames_dir(task_id)
    saved: list[Path] = []
    seen: list[int] = []
    deadline = time.monotonic() + duration_s
    while len(saved) < max_frames and time.monotonic() < deadline:
        frame = camera_manager.latest_frame(camera_id)
        if frame is not None:
            crop = crop_frame(frame, polygon)
            dest = out_dir / f"frame_{len(saved):04d}.jpg"
            if _save_frame(crop, dest, seen):
                saved.append(dest)
        time.sleep(interval_s)
    if not saved:
        raise ValueError(f"摄像头 {camera_id} 无可用帧（未运行或流未就绪）")
    logger.info("任务 %d 从摄像头 %d 采样 %d 张", task_id, camera_id, len(saved))
    return saved
