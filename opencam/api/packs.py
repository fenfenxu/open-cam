"""方案包 API：列出 / 详情 / 资产 / 安装 / 应用 / 卸载 / 隔离试跑 / 部署。

在线浏览为平台 stub，未配置时降级为内置包。
详情与资产走 PackCatalog；列表保持 installer brief 以兼容现有 Web/CLI。
试跑（trials）走 PackExperience 深模块：单会话、60 秒 TTL、无 DB/快照/VLM 副作用。
变更计划与原子应用走 PackDeployment；部署详情支持跨会话继续校准。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import (
    ApplyPlanOut,
    CameraOut,
    PackDeploymentOut,
    PackDeploymentResourcePatch,
    PackDetail,
    RuleOut,
)
from ..packs import installer
from ..packs.apply import apply_pack
from ..packs.catalog import catalog
from ..packs.deployment import DeploymentError, pack_deployment
from ..packs.experience import TrialOut, pack_experience, TrialError
from ..packs.manifest import PackError
from .cameras import camera_out

router = APIRouter(prefix="/api/packs", tags=["packs"])
trials_router = APIRouter(prefix="/api/pack-trials", tags=["packs"])
deployments_router = APIRouter(prefix="/api/pack-deployments", tags=["packs"])


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
    return {"online": False, "note": "市场平台接口尚未开放，敬请期待。", "packs": []}


class InstallRequest(BaseModel):
    source: str


@router.post("/install", status_code=201, summary="安装方案包", description="source 支持本地目录、.zip 文件路径或 http(s) URL。")
def install(body: InstallRequest):
    try:
        return installer.install(body.source)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/install-upload", status_code=201, summary="上传并安装方案包",
             description="上传 .zip 方案包并安装。")
def install_upload(file: UploadFile):
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
    expected_fingerprint: str | None = None


class PackApplyOut(BaseModel):
    cameras: list[CameraOut]
    rules: list[RuleOut]
    deployment_id: int | None = None


@router.post("/{pack_id}/apply-plan", response_model=ApplyPlanOut,
             summary="应用前变更计划",
             description="计算将创建/绑定的摄像头、规则与视频；返回内容指纹供确认时回传。")
def apply_plan(pack_id: str, body: ApplyRequest,
               session: Session = Depends(session_scope)):
    try:
        return pack_deployment.plan(pack_id, camera_id=body.camera_id,
                                    session=session)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
    except DeploymentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/{pack_id}/apply", response_model=PackApplyOut, status_code=201,
             summary="应用方案包",
             description="新包不传 camera_id，按包创建多路摄像头；旧包必须指定 camera_id。"
                         "可选 expected_fingerprint：不一致返回 409。")
def apply(pack_id: str, body: ApplyRequest,
          session: Session = Depends(session_scope)):
    try:
        result = apply_pack(
            pack_id, session, camera_id=body.camera_id,
            expected_fingerprint=body.expected_fingerprint)
    except PackError as exc:
        raise HTTPException(400, str(exc)) from exc
    except DeploymentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return PackApplyOut(
        cameras=[camera_out(c) for c in result.cameras],
        rules=[RuleOut.model_validate(r) for r in result.rules],
        deployment_id=result.deployment.id if result.deployment else None,
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
    if "/" in asset_id or "\\" in asset_id or ".." in asset_id or "%" in asset_id:
        raise HTTPException(404, "资产不存在")
    try:
        asset = catalog.open_asset(
            pack_id, asset_id, request.headers.get("range"))
    except PackError as exc:
        msg = str(exc)
        code = 404 if "不存在" in msg or "非法" in msg else 400
        raise HTTPException(code, msg) from exc

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


class TrialSourceIn(BaseModel):
    kind: Literal["pack", "video", "camera"] = "pack"
    video_id: int | None = None
    camera_id: int | None = None


class TrialStartIn(BaseModel):
    scene_id: str = Field(min_length=1)
    source: TrialSourceIn = Field(default_factory=TrialSourceIn)
    duration_sec: float | None = None


@router.post("/{pack_id}/trials", response_model=TrialOut, status_code=201,
             summary="发起本机隔离试跑",
             description="单场景实时试跑：全局最多一个主动会话，默认 60 秒 TTL。"
                         "不写库、不存快照、不调 VLM/通知。")
def start_trial(pack_id: str, body: TrialStartIn):
    try:
        return pack_experience.start(
            pack_id, body.scene_id,
            source_kind=body.source.kind,
            video_id=body.source.video_id,
            camera_id=body.source.camera_id,
            duration_sec=body.duration_sec)
    except TrialError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@trials_router.get("/{trial_id}", response_model=TrialOut,
                   summary="试跑状态",
                   description="规则状态、临时命中时间线、实际帧率与设备信息；已过期返回 410。")
def get_trial(trial_id: str):
    try:
        return pack_experience.inspect(trial_id)
    except TrialError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


_TRIAL_MJPEG_BOUNDARY = "frame"
_TRIAL_MJPEG_INTERVAL = 0.125


def _iter_trial_mjpeg(trial_id: str):
    while True:
        try:
            session, _ = pack_experience.mjpeg_state(trial_id)
        except TrialError:
            return
        payload = session.latest_jpeg()
        if payload:
            yield (
                b"--" + _TRIAL_MJPEG_BOUNDARY.encode()
                + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n" + payload + b"\r\n"
            )
        time.sleep(_TRIAL_MJPEG_INTERVAL)


@trials_router.get("/{trial_id}/live.mjpg", summary="试跑 MJPEG 实时画面",
                   description="约 8fps 推送叠加检测框/规则状态的画面；未运行 409，已过期 410。")
def trial_live_mjpeg(trial_id: str):
    try:
        pack_experience.mjpeg_state(trial_id)
    except TrialError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return StreamingResponse(
        _iter_trial_mjpeg(trial_id),
        media_type=f"multipart/x-mixed-replace; boundary={_TRIAL_MJPEG_BOUNDARY}",
    )


@trials_router.delete("/{trial_id}", status_code=204, summary="停止试跑",
                      description="幂等：已停止/过期/出错的试跑同样返回 204。")
def stop_trial(trial_id: str):
    try:
        pack_experience.stop(trial_id)
    except TrialError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@deployments_router.get("/{deployment_id}", response_model=PackDeploymentOut,
                        summary="部署详情",
                        description="资源映射与激活清单；资源缺失时 status=degraded。")
def get_deployment(deployment_id: int,
                   session: Session = Depends(session_scope)):
    try:
        return pack_deployment.get(deployment_id, session)
    except DeploymentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@deployments_router.patch(
    "/{deployment_id}/resources/{resource_id}",
    response_model=PackDeploymentOut,
    summary="标记资源配置完成",
    description="校准完成后标记 configured；规则资源会同时启用。"
                "全部规则配置完成且至少一路摄像头运行后进入 active。",
)
def patch_deployment_resource(
        deployment_id: int, resource_id: int,
        body: PackDeploymentResourcePatch,
        session: Session = Depends(session_scope)):
    try:
        return pack_deployment.mark_configured(
            deployment_id, resource_id, session, configured=body.configured)
    except DeploymentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
