"""方案包 API：列出 / 详情 / 资产 / 安装 / 应用 / 卸载。

在线浏览为平台 stub，未配置时降级为内置包。
详情与资产走 PackCatalog；列表保持 installer brief 以兼容现有 Web/CLI。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import CameraOut, PackDetail, RuleOut
from ..packs import installer
from ..packs.apply import apply_pack
from ..packs.catalog import catalog
from ..packs.manifest import PackError
from .cameras import camera_out

router = APIRouter(prefix="/api/packs", tags=["packs"])


@router.get("", summary="方案包列表", description="内置 + 已安装；同 id 时已安装覆盖内置。view=cards 返回规范化卡片。")
def list_packs(view: str = "brief"):
    """内置 + 已安装的方案包。

    默认 brief（兼容现有 Web/CLI）；`view=cards` 返回规范化 PackCard，
    含无效/不兼容包及其原因，供市场卡片使用。
    """
    if view == "cards":
        return catalog.list()
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


@router.post("/install-upload", status_code=201, summary="上传并安装方案包",
             description="上传 .zip 方案包并安装。")
def install_upload(file: UploadFile):
    """接收浏览器选择的 ZIP 文件，再复用本地 ZIP 安装流程。"""
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() != ".zip":
        raise HTTPException(400, "请上传 .zip 方案包")

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip") as uploaded:
            shutil.copyfileobj(file.file, uploaded)
            uploaded.flush()
            return installer.install(uploaded.name)
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


@router.get("/{pack_id}", response_model=PackDetail,
            summary="方案包详情",
            description="规范化 PackDetail；无效/不兼容包仍返回，availability 标明原因。")
def get_pack_detail(pack_id: str):
    try:
        return catalog.describe(pack_id)
    except PackError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{pack_id}/assets/{asset_id}",
            summary="方案包白名单资产",
            description="仅已声明且校验通过的资产；支持 Range、MIME、ETag。")
def get_pack_asset(pack_id: str, asset_id: str, request: Request):
    # 拒绝把路径伪装成 asset_id
    if "/" in asset_id or "\\" in asset_id or ".." in asset_id or "%" in asset_id:
        raise HTTPException(404, "资产不存在")
    try:
        asset = catalog.open_asset(
            pack_id, asset_id, request.headers.get("range"))
    except PackError as exc:
        msg = str(exc)
        code = 404 if "不存在" in msg or "非法" in msg else 400
        raise HTTPException(code, msg) from exc

    # 条件请求
    inm = request.headers.get("if-none-match")
    etag = f'"{asset.etag}"'
    if inm and inm.strip() == etag:
        return Response(status_code=304, headers={"ETag": etag})

    cache = ("public, max-age=86400"
             if asset.origin == "builtin"
             else "private, max-age=60")
    return FileResponse(
        asset.path,
        media_type=asset.media_type,
        headers={
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Cache-Control": cache,
            "Content-Disposition": "inline",
        },
    )


@router.delete("/{pack_id}", status_code=204, summary="卸载方案包", description="仅已安装的包可卸载；内置包返回 400。")
def uninstall(pack_id: str):
    try:
        installer.uninstall(pack_id)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
