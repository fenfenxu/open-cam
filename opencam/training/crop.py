"""固定区域裁剪：MVP 用多边形轴对齐外接矩形，用户只画一次区域。"""

from __future__ import annotations

import numpy as np


def crop_polygon(frame: np.ndarray, polygon: list[list[float]]) -> np.ndarray:
    """按多边形的轴对齐外接矩形裁剪；越界裁到画面内。空区域抛 ValueError。"""
    if not polygon:
        raise ValueError("区域多边形为空")
    h, w = frame.shape[:2]
    xs = [int(p[0]) for p in polygon]
    ys = [int(p[1]) for p in polygon]
    x1 = max(0, min(xs))
    y1 = max(0, min(ys))
    x2 = min(w, max(xs))
    y2 = min(h, max(ys))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("裁剪区域为空")
    return frame[y1:y2, x1:x2].copy()
