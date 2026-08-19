"""方案包应用：把规则模板实例化为摄像头的 DB 规则。

新包（manifest.cameras）按路复制演示片并创建摄像头；旧包仍打到指定 camera_id。
模板里 polygon 用 0-1 相对坐标，按画面分辨率换算为绝对像素。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CAMERA_STOPPED, Camera, Rule, Video, default_intent
from .installer import Pack, get_pack
from .manifest import PackError

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    cameras: list[Camera]
    rules: list[Rule]


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
               camera_id: int | None = None) -> ApplyResult:
    """应用方案包。新包创建多路摄像头；旧包必须指定 camera_id。"""
    pack = get_pack(pack_id)
    if pack is None:
        raise PackError(f"方案包不存在: {pack_id}")
    if pack.manifest.cameras is not None:
        if camera_id is not None:
            raise PackError("该方案会创建摄像头，不要指定 camera_id")
        return _apply_new_pack(pack, session)
    if camera_id is None:
        raise PackError("请指定要应用的摄像头")
    return _apply_legacy(pack, camera_id, session)


def _apply_legacy(pack: Pack, camera_id: int, session: Session) -> ApplyResult:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise PackError(f"摄像头不存在: {camera_id}")

    width, height = probe_resolution(camera.source_uri)
    created: list[Rule] = []
    for tpl in pack.rules:
        rule = Rule(
            camera_id=camera_id,
            name=tpl.name,
            type=tpl.type,
            params=scale_params(tpl.params, width, height),
            enabled=True,
            cooldown=tpl.cooldown,
            intent=default_intent(tpl.type),
        )
        session.add(rule)
        created.append(rule)
    session.commit()
    for rule in created:
        session.refresh(rule)
    logger.info("方案包 %s 已应用到摄像头 %d：%d 条规则 (%dx%d)",
                pack.manifest.id, camera_id, len(created), width, height)
    return ApplyResult(cameras=[camera], rules=created)


def _apply_new_pack(pack: Pack, session: Session) -> ApplyResult:
    created_cams: list[Camera] = []
    created_rules: list[Rule] = []
    used_names = {n for (n,) in session.query(Camera.name).all()}
    for cam in pack.manifest.cameras or []:
        dest = _copy_preview(pack.base_dir / cam.source)
        width, height = probe_resolution(str(dest))
        video = Video(
            filename=dest.name,
            path=str(dest),
            size_bytes=dest.stat().st_size,
            duration_sec=_duration_sec(dest),
            width=width,
            height=height,
            created_at=time.time(),
        )
        session.add(video)
        name = _unique_camera_name(f"{pack.manifest.name} · {cam.name}",
                                   used_names)
        used_names.add(name)
        camera = Camera(
            name=name,
            source_type="file",
            source_uri=str(dest),
            status=CAMERA_STOPPED,
        )
        session.add(camera)
        session.flush()
        created_cams.append(camera)
        for tpl in pack.rules:
            if tpl.camera != cam.id:
                continue
            rule = Rule(
                camera_id=camera.id,
                name=tpl.name,
                type=tpl.type,
                params=scale_params(tpl.params, width, height),
                enabled=True,
                cooldown=tpl.cooldown,
                intent=default_intent(tpl.type),
            )
            session.add(rule)
            created_rules.append(rule)
    session.commit()
    for camera in created_cams:
        session.refresh(camera)
    for rule in created_rules:
        session.refresh(rule)
    logger.info("方案包 %s 已创建 %d 路摄像头、%d 条规则",
                pack.manifest.id, len(created_cams), len(created_rules))
    return ApplyResult(cameras=created_cams, rules=created_rules)


def _copy_preview(src: Path) -> Path:
    """复制演示片到 uploads，basename 冲突则 stem_1.ext。"""
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / src.name
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while dest.exists():
        dest = upload_dir / f"{stem}_{n}{suffix}"
        n += 1
    shutil.copy2(src, dest)
    return dest


def _unique_camera_name(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    n = 2
    while f"{base} ({n})" in used:
        n += 1
    return f"{base} ({n})"


def _duration_sec(path: Path) -> float | None:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        if fps > 0 and frames > 0:
            return frames / fps
        return None
    finally:
        cap.release()
