"""PackDeployment：应用计划、原子应用与部署资源追踪的稳定接口。

对外只暴露四个操作：plan / apply / get_deployment / set_resource_configured。
内部隐藏新旧包双轨、视频复制、名称去重、分辨率探测、坐标换算、
数据库事务与失败文件回收、内容指纹一致性检查。

不变量：
- 应用要么全成功要么全失败：DB 异常回滚，已复制文件全部回收。
- 应用创建的摄像头保持 stopped，规则保持 disabled（待校准），
  逐路校准后经 set_resource_configured 明确启用。
- 归属只认 pack_deployment_resources 映射，不靠摄像头名称推断；
  资源被删/缺失时部署状态计算为 degraded。
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
from ..models import (
    CAMERA_RUNNING,
    CAMERA_STOPPED,
    DEPLOYMENT_ACTIVE,
    DEPLOYMENT_CONFIGURING,
    DEPLOYMENT_DEGRADED,
    DEPLOY_OWNERSHIP_BOUND,
    DEPLOY_OWNERSHIP_CREATED,
    RULE_TYPE_NAMES,
    ApplyPlanCameraOut,
    ApplyPlanOut,
    ApplyPlanRuleOut,
    Camera,
    PackDeployment,
    PackDeploymentOut,
    PackDeploymentResource,
    PackDeploymentResourceOut,
    Rule,
    Video,
)
from .catalog import compute_fingerprint, summarize_rule_params
from .installer import Pack, get_pack
from .manifest import PackError

logger = logging.getLogger(__name__)

# 旧格式包规范化后的虚拟机位 id（与 catalog 保持一致）
LEGACY_SLOT_ID = "default"

# 应用后的固定人工步骤（激活清单）
NEXT_STEPS = [
    "为每路摄像头更换真实视频源（RTSP 或视频文件）",
    "打开画面确认视角覆盖目标区域",
    "校准区域、计数线、阈值和生效时间",
    "逐路启用规则并启动摄像头",
    "运行一次验证，确认能看到预期事件或统计",
]


class DeploymentError(PackError):
    """部署相关错误；status 供 HTTP 层映射。"""

    status = 400


class PackNotFoundError(DeploymentError):
    status = 404


class TargetError(DeploymentError):
    """目标摄像头缺失/不允许、绑定非法。"""

    status = 422


class FingerprintMismatchError(DeploymentError):
    status = 409


class ResourceStateError(DeploymentError):
    """资源当前状态不允许该操作。"""

    status = 409


class DiskSpaceError(DeploymentError):
    status = 507


@dataclass
class ApplyOutcome:
    cameras: list[Camera]
    rules: list[Rule]
    deployment: PackDeployment
    resources: list[PackDeploymentResource]


# ---------- 变更计划 ----------


def plan(pack_id: str, session: Session, camera_id: int | None = None) -> ApplyPlanOut:
    """计算应用前变更计划；只读，不写库、不复制文件、不探测分辨率。"""
    pack = _require_pack(pack_id)
    fingerprint = compute_fingerprint(pack.base_dir)
    warnings = ["摄像头创建后保持停止，规则默认禁用，完成校准后逐路启用"]

    if pack.manifest.cameras is not None:
        if camera_id is not None:
            raise TargetError("该方案会创建摄像头，不要指定 camera_id")
        return _plan_new_pack(pack, session, fingerprint, warnings)
    if camera_id is None:
        raise TargetError("请选择要应用的摄像头")
    return _plan_legacy(pack, session, camera_id, fingerprint, warnings)


def _plan_new_pack(pack: Pack, session: Session, fingerprint: str,
                   warnings: list[str]) -> ApplyPlanOut:
    used_names = {n for (n,) in session.query(Camera.name).all()}
    cameras_out: list[ApplyPlanCameraOut] = []
    rules_out: list[ApplyPlanRuleOut] = []
    videos: list[str] = []
    for cam in pack.manifest.cameras or []:
        name = _unique_camera_name(f"{pack.manifest.name} · {cam.name}", used_names)
        used_names.add(name)
        rule_ids: list[str] = []
        for tpl in pack.rules:
            if tpl.camera != cam.id:
                continue
            rid = tpl.id or tpl.name
            rule_ids.append(rid)
            rules_out.append(ApplyPlanRuleOut(
                id=rid, name=tpl.name, type=tpl.type,
                type_label=RULE_TYPE_NAMES.get(tpl.type, tpl.type),
                slot_id=cam.id, summary=summarize_rule_params(tpl),
            ))
        src_name = Path(cam.source).name
        videos.append(src_name)
        cameras_out.append(ApplyPlanCameraOut(
            slot_id=cam.id, name=name, purpose=cam.purpose or "",
            video_filename=src_name, rule_ids=rule_ids,
        ))
    return ApplyPlanOut(
        pack_id=pack.manifest.id, pack_version=pack.manifest.version,
        fingerprint=fingerprint, mode="create_cameras",
        cameras=cameras_out, rules=rules_out, videos=videos,
        warnings=warnings, next_steps=list(NEXT_STEPS),
    )


def _plan_legacy(pack: Pack, session: Session, camera_id: int, fingerprint: str,
                 warnings: list[str]) -> ApplyPlanOut:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise TargetError(f"摄像头不存在: {camera_id}")
    warnings.append("规则默认禁用，确认区域与阈值后再启用，避免未校准即产生事件")
    rules_out = [
        ApplyPlanRuleOut(
            id=tpl.id or tpl.name, name=tpl.name, type=tpl.type,
            type_label=RULE_TYPE_NAMES.get(tpl.type, tpl.type),
            slot_id=LEGACY_SLOT_ID, summary=summarize_rule_params(tpl),
        )
        for tpl in pack.rules
    ]
    cameras_out = [ApplyPlanCameraOut(
        slot_id=LEGACY_SLOT_ID, name=camera.name, camera_id=camera.id,
        purpose="应用到已有摄像头",
        rule_ids=[r.id for r in rules_out],
    )]
    return ApplyPlanOut(
        pack_id=pack.manifest.id, pack_version=pack.manifest.version,
        fingerprint=fingerprint, mode="existing_camera",
        cameras=cameras_out, rules=rules_out,
        warnings=warnings,
        next_steps=["校准区域、计数线、阈值和生效时间",
                    "逐条启用规则",
                    "运行一次验证，确认能看到预期事件或统计"],
    )


# ---------- 原子应用 ----------


def apply(pack_id: str, session: Session, camera_id: int | None = None,
          expected_fingerprint: str | None = None) -> ApplyOutcome:
    """应用方案包并写入部署记录。

    expected_fingerprint 与当前包内容指纹不一致时拒绝应用（409），
    调用方需重新拉取变更计划确认。旧 CLI/HTTP 调用可省略指纹。
    """
    pack = _require_pack(pack_id)
    fingerprint = compute_fingerprint(pack.base_dir)
    if expected_fingerprint is not None and expected_fingerprint != fingerprint:
        raise FingerprintMismatchError(
            "方案包内容已变化，请重新查看变更计划后再应用")

    if pack.manifest.cameras is not None:
        if camera_id is not None:
            raise TargetError("该方案会创建摄像头，不要指定 camera_id")
        return _apply_new_pack(pack, session, fingerprint)
    if camera_id is None:
        raise TargetError("请指定要应用的摄像头")
    return _apply_legacy(pack, camera_id, session, fingerprint)


def _apply_legacy(pack: Pack, camera_id: int, session: Session,
                  fingerprint: str) -> ApplyOutcome:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise TargetError(f"摄像头不存在: {camera_id}")

    width, height = probe_resolution(camera.source_uri)
    now = time.time()
    try:
        created: list[Rule] = []
        for tpl in pack.rules:
            rule = Rule(
                camera_id=camera_id,
                name=tpl.name,
                type=tpl.type,
                params=scale_params(tpl.params, width, height),
                enabled=False,  # 待校准：确认区域与阈值后再启用
                cooldown=tpl.cooldown,
            )
            session.add(rule)
            created.append(rule)
        session.flush()
        deployment = _new_deployment(session, pack, fingerprint, now)
        resources = [PackDeploymentResource(
            deployment_id=deployment.id,
            camera_slot_id=LEGACY_SLOT_ID,
            kind="camera", resource_id=camera.id,
            ownership=DEPLOY_OWNERSHIP_BOUND, configured=False,
        )]
        for rule in created:
            resources.append(PackDeploymentResource(
                deployment_id=deployment.id,
                camera_slot_id=LEGACY_SLOT_ID,
                kind="rule", resource_id=rule.id,
                ownership=DEPLOY_OWNERSHIP_CREATED, configured=False,
            ))
        for res in resources:
            session.add(res)
        session.commit()
    except Exception:
        session.rollback()
        raise
    for rule in created:
        session.refresh(rule)
    session.refresh(deployment)
    logger.info("方案包 %s 已应用到摄像头 %d：%d 条规则 (%dx%d)，部署 %d",
                pack.manifest.id, camera_id, len(created), width, height,
                deployment.id)
    return ApplyOutcome(cameras=[camera], rules=created,
                        deployment=deployment, resources=resources)


def _apply_new_pack(pack: Pack, session: Session, fingerprint: str) -> ApplyOutcome:
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    _ensure_disk_space(pack, upload_dir)

    now = time.time()
    copied: list[Path] = []
    created_cams: list[Camera] = []
    created_rules: list[Rule] = []
    created_videos: list[Video] = []
    slot_of_camera: dict[int, str] = {}
    try:
        used_names = {n for (n,) in session.query(Camera.name).all()}
        for cam in pack.manifest.cameras or []:
            dest = _copy_preview(pack.base_dir / cam.source, upload_dir)
            copied.append(dest)
            width, height = probe_resolution(str(dest))
            video = Video(
                filename=dest.name,
                path=str(dest),
                size_bytes=dest.stat().st_size,
                duration_sec=_duration_sec(dest),
                width=width,
                height=height,
                created_at=now,
            )
            session.add(video)
            created_videos.append(video)
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
            slot_of_camera[camera.id] = cam.id
            for tpl in pack.rules:
                if tpl.camera != cam.id:
                    continue
                rule = Rule(
                    camera_id=camera.id,
                    name=tpl.name,
                    type=tpl.type,
                    params=scale_params(tpl.params, width, height),
                    enabled=False,  # 待校准：逐路完成校准后再启用
                    cooldown=tpl.cooldown,
                )
                session.add(rule)
                created_rules.append(rule)
        session.flush()
        deployment = _new_deployment(session, pack, fingerprint, now)
        resources: list[PackDeploymentResource] = []
        for video, camera in zip(created_videos, created_cams, strict=True):
            slot = slot_of_camera[camera.id]
            resources.append(PackDeploymentResource(
                deployment_id=deployment.id, camera_slot_id=slot,
                kind="video", resource_id=video.id,
                ownership=DEPLOY_OWNERSHIP_CREATED, configured=True,
            ))
            resources.append(PackDeploymentResource(
                deployment_id=deployment.id, camera_slot_id=slot,
                kind="camera", resource_id=camera.id,
                ownership=DEPLOY_OWNERSHIP_CREATED, configured=False,
            ))
        camera_slot = {c.id: slot_of_camera[c.id] for c in created_cams}
        for rule in created_rules:
            resources.append(PackDeploymentResource(
                deployment_id=deployment.id,
                camera_slot_id=camera_slot[rule.camera_id],
                kind="rule", resource_id=rule.id,
                ownership=DEPLOY_OWNERSHIP_CREATED, configured=False,
            ))
        for res in resources:
            session.add(res)
        session.commit()
    except Exception:
        # 全成功或全失败：DB 回滚 + 回收已复制文件，不留半套资源
        session.rollback()
        for path in copied:
            path.unlink(missing_ok=True)
        logger.warning("方案包 %s 应用失败，已回滚并回收 %d 个文件",
                       pack.manifest.id, len(copied))
        raise
    for camera in created_cams:
        session.refresh(camera)
    for rule in created_rules:
        session.refresh(rule)
    session.refresh(deployment)
    logger.info("方案包 %s 已创建 %d 路摄像头、%d 条规则，部署 %d",
                pack.manifest.id, len(created_cams), len(created_rules),
                deployment.id)
    return ApplyOutcome(cameras=created_cams, rules=created_rules,
                        deployment=deployment, resources=resources)


def _new_deployment(session: Session, pack: Pack, fingerprint: str,
                    now: float) -> PackDeployment:
    """创建部署记录并 flush 出 id（调用方负责后续资源与 commit）。"""
    deployment = PackDeployment(
        pack_id=pack.manifest.id,
        pack_version=pack.manifest.version,
        pack_digest=fingerprint,
        status=DEPLOYMENT_CONFIGURING,
        created_at=now,
        updated_at=now,
    )
    session.add(deployment)
    session.flush()
    return deployment


def _ensure_disk_space(pack: Pack, upload_dir: Path) -> None:
    """应用前预估所需空间：不足直接拒绝（507），不进入半复制状态。"""
    need = 0
    for cam in pack.manifest.cameras or []:
        src = pack.base_dir / cam.source
        if src.is_file():
            need += src.stat().st_size
    free = shutil.disk_usage(upload_dir).free
    if free < need:
        raise DiskSpaceError(
            f"应用所需磁盘空间不足：需要约 {need} 字节，剩余 {free} 字节")


# ---------- 部署查询与校准 ----------


def get_deployment(session: Session, deployment_id: int) -> PackDeploymentOut:
    """读取部署详情；状态按资源现状实时计算并修正（缺失 → degraded）。"""
    deployment = session.get(PackDeployment, deployment_id)
    if deployment is None:
        raise PackNotFoundError(f"部署不存在: {deployment_id}")
    resources = _resources_of(session, deployment_id)
    _sync_status(session, deployment, resources)
    return _deployment_out(session, deployment, resources)


def set_resource_configured(session: Session, deployment_id: int,
                            resource_id: int, configured: bool) -> PackDeploymentOut:
    """标记单个资源的校准完成状态；规则资源在完成校准时同步启用/禁用。"""
    deployment = session.get(PackDeployment, deployment_id)
    if deployment is None:
        raise PackNotFoundError(f"部署不存在: {deployment_id}")
    resource = session.get(PackDeploymentResource, resource_id)
    if resource is None or resource.deployment_id != deployment_id:
        raise PackNotFoundError(f"部署资源不存在: {resource_id}")
    if not _resource_exists(session, resource):
        resource.configured = False
        deployment.status = DEPLOYMENT_DEGRADED
        deployment.updated_at = time.time()
        session.commit()
        raise ResourceStateError("目标资源已缺失，无法更新校准状态")

    if resource.kind == "rule":
        rule = session.get(Rule, resource.resource_id)
        if rule is not None:
            if configured and not isinstance(rule.params, dict):
                raise ResourceStateError("规则参数无效，无法启用")
            rule.enabled = configured
    resource.configured = configured
    deployment.updated_at = time.time()
    _sync_status(session, deployment, _resources_of(session, deployment_id))
    session.commit()
    return _deployment_out(session, deployment,
                           _resources_of(session, deployment_id))


def _resources_of(session: Session, deployment_id: int) -> list[PackDeploymentResource]:
    return (session.query(PackDeploymentResource)
            .filter_by(deployment_id=deployment_id)
            .order_by(PackDeploymentResource.id)
            .all())


def _resource_exists(session: Session, resource: PackDeploymentResource) -> bool:
    model = {"camera": Camera, "rule": Rule, "video": Video}.get(resource.kind)
    if model is None:
        return False
    return session.get(model, resource.resource_id) is not None


def _sync_status(session: Session, deployment: PackDeployment,
                 resources: list[PackDeploymentResource]) -> None:
    """按资源现状修正部署状态：缺失 → degraded；全部校准且一路运行 → active。"""
    status = _compute_status(session, resources)
    if status != deployment.status:
        deployment.status = status
        deployment.updated_at = time.time()
        session.commit()


def _compute_status(session: Session,
                    resources: list[PackDeploymentResource]) -> str:
    if any(not _resource_exists(session, r) for r in resources):
        return DEPLOYMENT_DEGRADED
    gated = [r for r in resources if r.kind in ("camera", "rule")]
    if gated and all(r.configured for r in gated):
        for res in resources:
            if res.kind != "camera":
                continue
            camera = session.get(Camera, res.resource_id)
            if camera is not None and camera.status == CAMERA_RUNNING:
                return DEPLOYMENT_ACTIVE
    return DEPLOYMENT_CONFIGURING


def _deployment_out(session: Session, deployment: PackDeployment,
                    resources: list[PackDeploymentResource]) -> PackDeploymentOut:
    return PackDeploymentOut(
        id=deployment.id,
        pack_id=deployment.pack_id,
        pack_version=deployment.pack_version,
        pack_digest=deployment.pack_digest,
        status=deployment.status,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
        resources=[
            PackDeploymentResourceOut(
                id=r.id,
                camera_slot_id=r.camera_slot_id,
                kind=r.kind,
                resource_id=r.resource_id,
                ownership=r.ownership,
                configured=r.configured,
                exists=_resource_exists(session, r),
            )
            for r in resources
        ],
    )


# ---------- 内部工具 ----------


def _require_pack(pack_id: str) -> Pack:
    pack = get_pack(pack_id)
    if pack is None:
        raise PackNotFoundError(f"方案包不存在: {pack_id}")
    return pack


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


def _copy_preview(src: Path, upload_dir: Path) -> Path:
    """复制演示片到 uploads，basename 冲突则 stem_1.ext。"""
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
