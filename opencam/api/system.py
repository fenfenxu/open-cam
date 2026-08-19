"""系统信息 API：算力、模型、方案包统计，供控制台设置页展示；大模型配置可写。"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from .. import vlm_config
from ..config import settings
from ..hardware import memory_info, resolve_device

router = APIRouter(prefix="/api/system", tags=["system"])


class VlmSettingsUpdate(BaseModel):
    api_key: Optional[str] = Field(
        None, description="不传则保留原值；空字符串清除本机保存的 key")
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: Optional[float] = Field(None, ge=1, le=300)


@router.get("/info", summary="系统信息",
            description="推理设备、内存、检测器配置、方案包统计与 VLM 配置状态。")
def system_info():
    device = resolve_device(settings.device)
    from ..packs.installer import list_packs  # 避免循环 import，用到再引

    packs = list_packs()
    vlm = vlm_config.resolve_review()
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
        "vlm_configured": bool(vlm.api_key),
        "vlm_model": vlm.model,
        "platform_base_url": settings.platform_base_url,
        "data_dir": str(settings.data_dir),
    }


@router.get("/vlm", summary="大模型配置（不返回完整 api_key）")
def get_vlm_settings():
    return vlm_config.public_view()


@router.put("/vlm", summary="保存大模型配置到本机数据目录")
def put_vlm_settings(body: VlmSettingsUpdate):
    kwargs = {}
    if "api_key" in body.model_fields_set:
        kwargs["api_key"] = body.api_key or ""
    if "base_url" in body.model_fields_set:
        kwargs["base_url"] = body.base_url or ""
    if "model" in body.model_fields_set:
        kwargs["model"] = body.model or ""
    if "timeout" in body.model_fields_set:
        kwargs["timeout"] = body.timeout
    return vlm_config.update_overlay(**kwargs)


@router.post("/vlm/test", summary="用当前配置测一次对话，确认 Key 可用")
def test_vlm_settings():
    try:
        return vlm_config.ping()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            400, f"接口返回 {exc.response.status_code}，请检查 Key、地址和模型名") from None
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"连不上大模型：{exc}") from None


@router.get("/health", summary="升级质检与健康检查",
            description="schema 版本/完整性、数据与快照目录可写、近期事件快照文件抽查。"
                        "全部通过返回 200，否则 503 并附问题明细。升级后可用 `opencam system doctor` 调用。")
def system_health():
    from ..doctor import check_health  # 用到再引

    result = check_health()
    return JSONResponse(result, status_code=200 if result["ok"] else 503)
