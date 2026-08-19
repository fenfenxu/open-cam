"""方案包应用：把规则模板实例化为某摄像头的 DB 规则。

模板里 polygon 用 0-1 相对坐标，按摄像头实际画面分辨率换算为绝对像素。
应用后就是普通规则，用户可在 Rules 页自由修改。
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
from sqlalchemy.orm import Session

from ..models import Camera, Rule
from .installer import get_pack
from .manifest import PackError

logger = logging.getLogger(__name__)


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


def apply_pack(pack_id: str, camera_id: int, session: Session) -> list[Rule]:
    """把包的规则模板实例化为摄像头规则，返回新建的规则列表。"""
    pack = get_pack(pack_id)
    if pack is None:
        raise PackError(f"方案包不存在: {pack_id}")
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise PackError(f"摄像头不存在: {camera_id}")

    width, height = probe_resolution(camera.source_uri)
    created: list[Rule] = []
    for tpl in pack.rules:
        rule = Rule(
            camera_id=camera_id,
            name=tpl.name,  # 模板中文名，如"后厨区域入侵"
            type=tpl.type,
            params=scale_params(tpl.params, width, height),
            enabled=True,
            cooldown=tpl.cooldown,
        )
        session.add(rule)
        created.append(rule)
    session.commit()
    for rule in created:
        session.refresh(rule)
    logger.info("方案包 %s 已应用到摄像头 %d：%d 条规则 (%dx%d)",
                pack_id, camera_id, len(created), width, height)
    return created
