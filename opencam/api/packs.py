"""方案包 API：列出 / 安装 / 应用 / 卸载。在线浏览为平台 stub，未配置时降级为内置包。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import CameraOut, RuleOut
from ..packs import installer
from ..packs.apply import apply_pack
from ..packs.manifest import PackError
from .cameras import camera_out

router = APIRouter(prefix="/api/packs", tags=["packs"])


@router.get("", summary="方案包列表", description="内置 + 已安装；同 id 时已安装覆盖内置。")
def list_packs():
    """内置 + 已安装的方案包。"""
    return installer.list_packs()


@router.get("/online", summary="在线市场（stub）", description="平台未配置时优雅降级为只显示内置包。")
def browse_online():
    """在线市场（stub）：平台未配置时优雅降级为只显示内置包。"""
    if not settings.platform_base_url:
        return {
            "online": False,
            "note": "未配置市场平台（platform_base_url），当前仅显示内置方案包。",
            "packs": [p for p in installer.list_packs() if p["origin"] == "builtin"],
        }
    # 平台接口预留：配置后在此接入真实市场
    return {"online": False, "note": "市场平台接口尚未开放，敬请期待。", "packs": []}


class InstallRequest(BaseModel):
    source: str  # 本地目录 / zip 路径 / URL


@router.post("/install", status_code=201, summary="安装方案包", description="source 支持本地目录、.zip 文件路径或 http(s) URL。")
def install(body: InstallRequest):
    try:
        return installer.install(body.source)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc


class ApplyRequest(BaseModel):
    camera_id: int | None = None


class PackApplyOut(BaseModel):
    cameras: list[CameraOut]
    rules: list[RuleOut]


@router.post("/{pack_id}/apply", response_model=PackApplyOut, status_code=201,
             summary="应用方案包",
             description="新包不传 camera_id，按包创建多路摄像头；旧包必须指定 camera_id。")
def apply(pack_id: str, body: ApplyRequest,
          session: Session = Depends(session_scope)):
    try:
        result = apply_pack(pack_id, session, camera_id=body.camera_id)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
    return PackApplyOut(
        cameras=[camera_out(c) for c in result.cameras],
        rules=[RuleOut.model_validate(r) for r in result.rules],
    )


@router.delete("/{pack_id}", status_code=204, summary="卸载方案包", description="仅已安装的包可卸载；内置包返回 400。")
def uninstall(pack_id: str):
    try:
        installer.uninstall(pack_id)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
