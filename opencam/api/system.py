"""系统信息 API：算力、模型、方案包统计，供控制台设置页展示。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import settings
from ..hardware import memory_info, resolve_device

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info", summary="系统信息", description="推理设备、内存、检测器配置、方案包统计与 VLM 配置状态。")
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
        "data_dir": str(settings.data_dir),
    }


@router.get("/health", summary="升级质检与健康检查",
            description="schema 版本/完整性、数据与快照目录可写、近期事件快照文件抽查。"
                        "全部通过返回 200，否则 503 并附问题明细。升级后可用 `opencam system doctor` 调用。")
def system_health():
    from ..doctor import check_health  # 用到再引

    result = check_health()
    return JSONResponse(result, status_code=200 if result["ok"] else 503)
