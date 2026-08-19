"""从摄像头录像或上传视频抽帧，按感知哈希（pHash）去重后写入 frames/。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .storage import task_exists, write_frame

logger = logging.getLogger(__name__)


def perceptual_hash(frame: np.ndarray, hash_size: int = 8) -> int:
    """DCT 感知哈希，64 bit。结构相似的画面汉明距离小。"""
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    size = hash_size * 4
    resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    low = dct[:hash_size, :hash_size]
    ac = low.flatten()[1:]
    med = float(np.median(ac)) if ac.size else 0.0
    bits = (low.flatten() > med).astype(np.uint8)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def extract_frames(
    task_id: str,
    source: Path,
    *,
    max_frames: int = 120,
    hamming_threshold: int = 8,
) -> dict[str, Any]:
    if not task_exists(task_id):
        raise FileNotFoundError(task_id)
    if not source.is_file():
        raise FileNotFoundError(str(source))

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ValueError(f"无法打开视频: {source}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(round(fps)))
        if total and total / step < 3:
            step = 1

        kept: list[int] = []
        written = 0
        skipped_dup = 0
        scanned = 0
        frame_i = 0
        while written < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_i % step != 0:
                frame_i += 1
                continue
            scanned += 1
            digest = perceptual_hash(frame)
            if any(hamming_distance(digest, prev) <= hamming_threshold
                   for prev in kept):
                skipped_dup += 1
                frame_i += 1
                continue
            write_frame(task_id, f"{written:05d}.jpg", frame)
            kept.append(digest)
            written += 1
            frame_i += 1
    finally:
        cap.release()

    return {
        "written": written,
        "skipped_dup": skipped_dup,
        "scanned": scanned,
        "source": str(source),
    }
