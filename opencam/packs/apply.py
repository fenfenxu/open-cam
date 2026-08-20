"""方案包应用：坐标换算辅助与兼容入口。

原子应用、部署追踪与指纹确认见 `packs.deployment`。
本模块保留 `probe_resolution` / `scale_params` / `apply_pack`，
供体验模块与既有测试继续直接 import。
"""

from __future__ import annotations

from typing import Any

import cv2
from sqlalchemy.orm import Session

from .manifest import PackError


def probe_resolution(source_uri: str) -> tuple[int, int]:
    """探测视频源分辨率：先读元数据，读不到就抓一帧。"""
    cap = cv2.VideoCapture(source_uri)
    try:
        if not cap.isOpened():
            raise PackError(f"无法打开视频源: {source_uri}")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if w <= 0 or h <= 0:
            ok, frame = cap.read()
            if not ok:
                raise PackError(f"无法读取视频源画面: {source_uri}")
            h, w = frame.shape[:2]
        return w, h
    finally:
        cap.release()


def scale_params(params: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """把 params 里 polygon/line 的 0-1 相对坐标换算为绝对像素；其余参数原样保留。"""
    out = dict(params)
    for key in ("polygon", "line"):
        coords = out.get(key)
        if coords:
            out[key] = [[round(x * width, 1), round(y * height, 1)]
                        for x, y in coords]
    return out


def apply_pack(pack_id: str, session: Session,
               camera_id: int | None = None,
               expected_fingerprint: str | None = None):
    """兼容入口：转发到 PackDeployment.apply。"""
    from .deployment import pack_deployment

    return pack_deployment.apply(
        pack_id, session, camera_id=camera_id,
        expected_fingerprint=expected_fingerprint)
