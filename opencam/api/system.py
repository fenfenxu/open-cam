"""系统信息 API：算力、模型、方案包统计，供控制台设置页展示。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import settings
from ..hardware import memory_info, resolve_device

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info")
def system_info():
    device = resolve_device(settings.device)
    from ..packs.installer import list_packs  # 避免循环 import，用到再引

    packs = list_packs()
    return {
        "version": __version__,
        "device": device,
        "device_config": settings.device,
        **memory_info(device),
        "yolo_model": settings.yolo_model,
        "detector": settings.detector,
        "detect_fps": settings.detect_fps,
        "packs_available": len(packs),
        "packs_installed": sum(1 for p in packs if p["origin"] == "installed"),
        "vlm_configured": bool(settings.vlm_api_key),
        "vlm_model": settings.vlm_model,
        "platform_base_url": settings.platform_base_url,
    }
