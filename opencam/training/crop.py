"""固定区域裁剪：一期对象位置固定，用户画一次区域，抽帧后只裁这块。"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def _to_pixel_xyxy(region: dict[str, Any], width: int, height: int
                   ) -> tuple[int, int, int, int]:
    """把区域转成像素 xyxy，兼容归一化 xywh / 多边形。"""
    if not region:
        return 0, 0, width, height

    if "polygon" in region and region["polygon"]:
        pts = np.array(region["polygon"], dtype=np.float32)
        if pts.ndim != 2 or pts.shape[1] != 2:
            return 0, 0, width, height
        if float(np.nanmax(pts)) <= 1.0:
            pts[:, 0] *= width
            pts[:, 1] *= height
        x, y, bw, bh = cv2.boundingRect(pts.astype(np.int32))
        return x, y, x + bw, y + bh

    x = float(region.get("x", 0))
    y = float(region.get("y", 0))
    w = float(region.get("w", 1))
    h = float(region.get("h", 1))
    # 值都 ≤1 视为归一化；否则当像素
    if max(x, y, w, h) <= 1.0:
        x1 = int(x * width)
        y1 = int(y * height)
        x2 = int((x + w) * width)
        y2 = int((y + h) * height)
    else:
        x1, y1 = int(x), int(y)
        x2, y2 = int(x + w), int(y + h)
    return x1, y1, x2, y2


def crop_frame(frame: np.ndarray, region: dict[str, Any] | None) -> np.ndarray:
    """按固定区域裁剪；区域为空则返回原图。"""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = _to_pixel_xyxy(region or {}, width, height)
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return frame[y1:y2, x1:x2]
