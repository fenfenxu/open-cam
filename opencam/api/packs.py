"""方案包 API：列出 / 安装 / 应用 / 卸载。在线浏览为平台 stub，未配置时降级为内置包。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import RuleOut
from ..packs import installer
from ..packs.apply import apply_pack
from ..packs.manifest import PackError

router = APIRouter(prefix="/api/packs", tags=["packs"])


@router.get("")
def list_packs():
    """内置 + 已安装的方案包。"""
    return installer.list_packs()


@router.get("/online")
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


@router.post("/install", status_code=201)
def install(body: InstallRequest):
    try:
        return installer.install(body.source)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc


class ApplyRequest(BaseModel):
    camera_id: int


@router.post("/{pack_id}/apply", response_model=list[RuleOut], status_code=201)
def apply(pack_id: str, body: ApplyRequest,
          session: Session = Depends(session_scope)):
    try:
        return apply_pack(pack_id, body.camera_id, session)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{pack_id}", status_code=204)
def uninstall(pack_id: str):
    try:
        installer.uninstall(pack_id)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
