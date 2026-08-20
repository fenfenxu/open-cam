"""PackDeployment：方案包变更计划、原子应用与部署追踪（深模块）。

plan / apply / get / mark_configured 掩盖新旧包双轨、文件 staging、
内容指纹确认与资源归属。HTTP / CLI / Web 只跨本模块稳定接口。

约束（计划 Task 5 / 全局验收 6.4）：

- 应用前可生成 ApplyPlan；Web 回传 expected_fingerprint，不一致返回 409。
- DB 事务失败或异常时回收已复制文件，不留半套资源。
- 新流程规则默认 disabled（待校准）；PATCH 校准完成后再启用。
- 资源缺失时部署状态修正为 degraded；卸载包不级联删除部署资源。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..model_assets import register_pack_profiles
from ..models import (
    CAMERA_RUNNING,
    CAMERA_STOPPED,
    ApplyPlanCameraOut,
    ApplyPlanOut,
    ApplyPlanRuleOut,
    ApplyPlanVideoOut,
    AnalysisProfile,
    Camera,
    CameraBinding,
    DEPLOY_ACTIVE,
    DEPLOY_CONFIGURING,
    DEPLOY_DEGRADED,
    DEPLOY_KIND_CAMERA,
    DEPLOY_KIND_RULE,
    DEPLOY_KIND_VIDEO,
    DEPLOY_OWNERSHIP_BOUND,
    DEPLOY_OWNERSHIP_CREATED,
    PackDeployment,
    PackDeploymentOut,
    PackDeploymentResource,
    PackDeploymentResourceOut,
    Rule,
    Video,
    default_rule_capabilities,
)
from .apply import probe_resolution, scale_params
from .catalog import compute_fingerprint
from .installer import Pack, get_pack
from .manifest import PackError

logger = logging.getLogger(__name__)

_LEGACY_SLOT = "default"

_WILL_NOT = (
    "不自动启动摄像头",
    "不覆盖或删除已有摄像头与规则",
    "不启用规则（需逐路校准后明确启用）",
)

_NEXT_STEPS = (
    "为每路摄像头更换真实 RTSP 或视频文件源",
    "打开画面确认视角",
    "校准区域、计数线、阈值后标记完成",
    "启用规则并启动摄像头",
    "验证能看到预期事件或统计",
)


class DeploymentError(Exception):
    """部署相关错误；status 对应 HTTP 状态码。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ApplyResult:
    cameras: list[Camera]
    rules: list[Rule]
    deployment: PackDeployment | None = None
    videos: list[Video] = field(default_factory=list)


class PackDeploymentService:
    """方案部署深模块单例。"""

    def plan(self, pack_id: str, camera_id: int | None = None,
             session: Session | None = None) -> ApplyPlanOut:
        pack = _require_pack(pack_id)
        fingerprint = compute_fingerprint(pack.base_dir)
        if pack.manifest.cameras is not None:
            if camera_id is not None:
                raise PackError("该方案会创建摄像头，不要指定 camera_id")
            return _plan_new(pack, fingerprint)
        if camera_id is None:
            raise PackError("请指定要应用的摄像头")
        if session is None:
            raise PackError("旧包计划需要数据库会话以校验摄像头")
        camera = session.get(Camera, camera_id)
        if camera is None:
            raise PackError(f"摄像头不存在: {camera_id}")
        return _plan_legacy(pack, fingerprint, camera)

    def apply(self, pack_id: str, session: Session,
              camera_id: int | None = None,
              expected_fingerprint: str | None = None) -> ApplyResult:
        pack = _require_pack(pack_id)
        fingerprint = compute_fingerprint(pack.base_dir)
        if expected_fingerprint is not None and expected_fingerprint != fingerprint:
            raise DeploymentError(
                409, "方案包内容已变化，请重新查看变更计划后再确认")

        # 内置包也必须先把方案/阶段登记为可复用领域对象；安装流程已幂等完成这一步。
        register_pack_profiles(session, pack.base_dir, pack.manifest.id)

        if pack.manifest.cameras is not None:
            if camera_id is not None:
                raise PackError("该方案会创建摄像头，不要指定 camera_id")
            return _apply_new(pack, session, fingerprint)
        if camera_id is None:
            raise PackError("请指定要应用的摄像头")
        return _apply_legacy(pack, session, camera_id, fingerprint)

    def get(self, deployment_id: int, session: Session) -> PackDeploymentOut:
        dep = session.get(PackDeployment, deployment_id)
        if dep is None:
            raise DeploymentError(404, f"部署不存在: {deployment_id}")
        return _deployment_out(dep, session, persist_status=True)

    def mark_configured(self, deployment_id: int, resource_row_id: int,
                        session: Session,
                        configured: bool = True) -> PackDeploymentOut:
        dep = session.get(PackDeployment, deployment_id)
        if dep is None:
            raise DeploymentError(404, f"部署不存在: {deployment_id}")
        row = session.get(PackDeploymentResource, resource_row_id)
        if row is None or row.deployment_id != deployment_id:
            raise DeploymentError(404, f"部署资源不存在: {resource_row_id}")

        # 资源已缺失时不允许标记完成
        missing = _resource_missing(row, session)
        if missing:
            raise DeploymentError(409, "资源已缺失，无法标记校准完成")

        row.configured = configured
        if row.kind == DEPLOY_KIND_RULE and configured:
            rule = session.get(Rule, row.resource_id)
            if rule is None:
                raise DeploymentError(409, "规则不存在，无法启用")
            if not isinstance(rule.params, dict):
                raise DeploymentError(409, "规则参数无效，无法启用")
            rule.enabled = True
        elif row.kind == DEPLOY_KIND_RULE and not configured:
            rule = session.get(Rule, row.resource_id)
            if rule is not None:
                rule.enabled = False

        dep.updated_at = time.time()
        session.commit()
        session.refresh(dep)
        return _deployment_out(dep, session, persist_status=True)


def apply_pack(pack_id: str, session: Session,
               camera_id: int | None = None,
               expected_fingerprint: str | None = None) -> ApplyResult:
    """兼容入口：转发到 PackDeployment.apply。"""
    return pack_deployment.apply(
        pack_id, session, camera_id=camera_id,
        expected_fingerprint=expected_fingerprint)


pack_deployment = PackDeploymentService()


# ---------- plan ----------


def _require_pack(pack_id: str) -> Pack:
    pack = get_pack(pack_id)
    if pack is None:
        raise PackError(f"方案包不存在: {pack_id}")
    return pack


def _plan_new(pack: Pack, fingerprint: str) -> ApplyPlanOut:
    cameras: list[ApplyPlanCameraOut] = []
    rules: list[ApplyPlanRuleOut] = []
    videos: list[ApplyPlanVideoOut] = []
    for cam in pack.manifest.cameras or []:
        cameras.append(ApplyPlanCameraOut(
            slot_id=cam.id,
            name=f"{pack.manifest.name} · {cam.name}",
            action="create",
            source_hint=cam.source,
        ))
        videos.append(ApplyPlanVideoOut(
            filename=Path(cam.source).name,
            camera_slot_id=cam.id,
            action="copy",
        ))
        for tpl in pack.rules:
            if tpl.camera != cam.id:
                continue
            rules.append(ApplyPlanRuleOut(
                name=tpl.name,
                type=tpl.type,
                camera_slot_id=cam.id,
            ))
    warnings = [
        "将创建新的摄像头与规则；演示视频会复制到本机 uploads。",
        "摄像头保持停止，规则保持禁用，需按激活清单完成上线。",
    ]
    return ApplyPlanOut(
        pack_id=pack.manifest.id,
        pack_version=pack.manifest.version,
        fingerprint=fingerprint,
        mode="create_cameras",
        cameras=cameras,
        rules=rules,
        videos=videos,
        will_not=list(_WILL_NOT),
        next_steps=list(_NEXT_STEPS),
        warnings=warnings,
    )


def _plan_legacy(pack: Pack, fingerprint: str, camera: Camera) -> ApplyPlanOut:
    cameras = [ApplyPlanCameraOut(
        slot_id=_LEGACY_SLOT,
        name=camera.name,
        action="bind",
        source_hint=camera.source_uri,
    )]
    rules = [
        ApplyPlanRuleOut(
            name=tpl.name,
            type=tpl.type,
            camera_slot_id=_LEGACY_SLOT,
        )
        for tpl in pack.rules
    ]
    warnings = [
        f"将把 {len(rules)} 条规则绑定到已有摄像头「{camera.name}」。",
        "规则默认禁用，校准完成前不会参与正式判断。",
    ]
    return ApplyPlanOut(
        pack_id=pack.manifest.id,
        pack_version=pack.manifest.version,
        fingerprint=fingerprint,
        mode="existing_camera",
        cameras=cameras,
        rules=rules,
        videos=[],
        will_not=list(_WILL_NOT),
        next_steps=list(_NEXT_STEPS),
        warnings=warnings,
    )


# ---------- apply ----------


def _apply_new(pack: Pack, session: Session, fingerprint: str) -> ApplyResult:
    staged: list[Path] = []
    created_cams: list[Camera] = []
    created_rules: list[Rule] = []
    created_videos: list[Video] = []
    mappings: list[tuple[str, str, int, str]] = []  # slot, kind, id, ownership

    try:
        _ensure_disk_space(pack)
        used_names = {n for (n,) in session.query(Camera.name).all()}
        now = time.time()
        dep = PackDeployment(
            pack_id=pack.manifest.id,
            pack_version=pack.manifest.version,
            pack_digest=fingerprint,
            status=DEPLOY_CONFIGURING,
            created_at=now,
            updated_at=now,
        )
        session.add(dep)
        session.flush()

        for cam in pack.manifest.cameras or []:
            dest = _copy_preview(pack.base_dir / cam.source)
            staged.append(dest)
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
            session.flush()
            created_videos.append(video)
            mappings.append((cam.id, DEPLOY_KIND_VIDEO, video.id,
                             DEPLOY_OWNERSHIP_CREATED))

            name = _unique_camera_name(
                f"{pack.manifest.name} · {cam.name}", used_names)
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
            mappings.append((cam.id, DEPLOY_KIND_CAMERA, camera.id,
                             DEPLOY_OWNERSHIP_CREATED))

            _bind_pack_profile(session, camera.id, pack.manifest.id)

            for tpl in pack.rules:
                if tpl.camera != cam.id:
                    continue
                rule = Rule(
                    camera_id=camera.id,
                    name=tpl.name,
                    type=tpl.type,
                    params=scale_params(tpl.params, width, height),
                    capabilities=tpl.capabilities or default_rule_capabilities(
                        tpl.type, tpl.params),
                    enabled=False,
                    cooldown=tpl.cooldown,
                )
                session.add(rule)
                session.flush()
                created_rules.append(rule)
                mappings.append((cam.id, DEPLOY_KIND_RULE, rule.id,
                                 DEPLOY_OWNERSHIP_CREATED))

        for slot, kind, rid, ownership in mappings:
            session.add(PackDeploymentResource(
                deployment_id=dep.id,
                camera_slot_id=slot,
                kind=kind,
                resource_id=rid,
                ownership=ownership,
                configured=False,
            ))
        session.commit()
    except Exception:
        session.rollback()
        _cleanup_staged(staged)
        raise

    for camera in created_cams:
        session.refresh(camera)
    for rule in created_rules:
        session.refresh(rule)
    session.refresh(dep)
    logger.info("方案包 %s 已创建部署 #%d：%d 路摄像头、%d 条规则",
                pack.manifest.id, dep.id, len(created_cams), len(created_rules))
    return ApplyResult(
        cameras=created_cams, rules=created_rules,
        deployment=dep, videos=created_videos)


def _apply_legacy(pack: Pack, session: Session, camera_id: int,
                  fingerprint: str) -> ApplyResult:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise PackError(f"摄像头不存在: {camera_id}")

    created: list[Rule] = []
    try:
        width, height = probe_resolution(camera.source_uri)
        now = time.time()
        dep = PackDeployment(
            pack_id=pack.manifest.id,
            pack_version=pack.manifest.version,
            pack_digest=fingerprint,
            status=DEPLOY_CONFIGURING,
            created_at=now,
            updated_at=now,
        )
        session.add(dep)
        session.flush()

        session.add(PackDeploymentResource(
            deployment_id=dep.id,
            camera_slot_id=_LEGACY_SLOT,
            kind=DEPLOY_KIND_CAMERA,
            resource_id=camera.id,
            ownership=DEPLOY_OWNERSHIP_BOUND,
            configured=False,
        ))
        _bind_pack_profile(session, camera.id, pack.manifest.id)

        for tpl in pack.rules:
            rule = Rule(
                camera_id=camera_id,
                name=tpl.name,
                type=tpl.type,
                params=scale_params(tpl.params, width, height),
                capabilities=tpl.capabilities or default_rule_capabilities(
                    tpl.type, tpl.params),
                enabled=False,
                cooldown=tpl.cooldown,
            )
            session.add(rule)
            session.flush()
            created.append(rule)
            session.add(PackDeploymentResource(
                deployment_id=dep.id,
                camera_slot_id=_LEGACY_SLOT,
                kind=DEPLOY_KIND_RULE,
                resource_id=rule.id,
                ownership=DEPLOY_OWNERSHIP_CREATED,
                configured=False,
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise

    for rule in created:
        session.refresh(rule)
    session.refresh(dep)
    session.refresh(camera)
    logger.info("方案包 %s 已绑定摄像头 %d 并创建部署 #%d：%d 条规则 (%dx%d)",
                pack.manifest.id, camera_id, dep.id, len(created), width, height)
    return ApplyResult(cameras=[camera], rules=created, deployment=dep)


def _bind_pack_profile(session: Session, camera_id: int, pack_id: str) -> None:
    """若方案声明了分析方案，应用时把它绑定到对应摄像头。"""
    profile = (session.query(AnalysisProfile)
               .filter_by(solution_pack_id=pack_id, status="active")
               .order_by(AnalysisProfile.id)
               .first())
    if profile is None:
        return
    binding = session.query(CameraBinding).filter_by(camera_id=camera_id).first()
    now = time.time()
    if binding is None:
        session.add(CameraBinding(
            camera_id=camera_id,
            analysis_profile_id=profile.id,
            profile_version=profile.version,
            enabled=False,
            created_at=now,
            updated_at=now,
        ))
    else:
        binding.analysis_profile_id = profile.id
        binding.profile_version = profile.version
        binding.enabled = False
        binding.updated_at = now


# ---------- deployment inspect ----------


def _deployment_out(dep: PackDeployment, session: Session,
                    persist_status: bool = False) -> PackDeploymentOut:
    rows = (session.query(PackDeploymentResource)
            .filter_by(deployment_id=dep.id)
            .order_by(PackDeploymentResource.id)
            .all())
    resources: list[PackDeploymentResourceOut] = []
    any_missing = False
    camera_running = False
    all_rules_configured = True
    has_rule = False

    for row in rows:
        missing = _resource_missing(row, session)
        any_missing = any_missing or missing
        label, detail = _resource_label(row, session)
        if row.kind == DEPLOY_KIND_CAMERA and not missing:
            cam = session.get(Camera, row.resource_id)
            if cam is not None and cam.status == CAMERA_RUNNING:
                camera_running = True
        if row.kind == DEPLOY_KIND_RULE:
            has_rule = True
            if not row.configured:
                all_rules_configured = False
        resources.append(PackDeploymentResourceOut(
            id=row.id,
            camera_slot_id=row.camera_slot_id,
            kind=row.kind,
            resource_id=row.resource_id,
            ownership=row.ownership,
            configured=row.configured,
            missing=missing,
            label=label,
            detail=detail,
        ))

    if any_missing:
        status = DEPLOY_DEGRADED
    elif has_rule and all_rules_configured and camera_running:
        status = DEPLOY_ACTIVE
    else:
        status = DEPLOY_CONFIGURING

    if persist_status and status != dep.status:
        dep.status = status
        dep.updated_at = time.time()
        session.commit()
        session.refresh(dep)

    return PackDeploymentOut(
        id=dep.id,
        pack_id=dep.pack_id,
        pack_version=dep.pack_version,
        pack_digest=dep.pack_digest,
        status=status,
        created_at=dep.created_at,
        updated_at=dep.updated_at,
        resources=resources,
        activation_steps=list(_NEXT_STEPS),
    )


def _resource_missing(row: PackDeploymentResource, session: Session) -> bool:
    if row.kind == DEPLOY_KIND_CAMERA:
        return session.get(Camera, row.resource_id) is None
    if row.kind == DEPLOY_KIND_RULE:
        return session.get(Rule, row.resource_id) is None
    if row.kind == DEPLOY_KIND_VIDEO:
        video = session.get(Video, row.resource_id)
        if video is None:
            return True
        return not Path(video.path).exists()
    return True


def _resource_label(row: PackDeploymentResource,
                    session: Session) -> tuple[str, dict[str, Any]]:
    if row.kind == DEPLOY_KIND_CAMERA:
        cam = session.get(Camera, row.resource_id)
        if cam is None:
            return f"摄像头 #{row.resource_id}（已缺失）", {}
        return cam.name, {"status": cam.status, "source_type": cam.source_type}
    if row.kind == DEPLOY_KIND_RULE:
        rule = session.get(Rule, row.resource_id)
        if rule is None:
            return f"规则 #{row.resource_id}（已缺失）", {}
        return rule.name or rule.type, {
            "enabled": rule.enabled, "type": rule.type, "camera_id": rule.camera_id,
        }
    if row.kind == DEPLOY_KIND_VIDEO:
        video = session.get(Video, row.resource_id)
        if video is None:
            return f"视频 #{row.resource_id}（已缺失）", {}
        return video.filename, {"path_exists": Path(video.path).exists()}
    return f"{row.kind} #{row.resource_id}", {}


# ---------- file helpers ----------


def _ensure_disk_space(pack: Pack) -> None:
    """粗略检查 uploads 所在磁盘是否还能放下演示片副本。"""
    sources = []
    for cam in pack.manifest.cameras or []:
        src = pack.base_dir / cam.source
        if src.is_file():
            sources.append(src)
    if not sources:
        return
    need = sum(s.stat().st_size for s in sources) + 1024 * 1024
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(upload_dir).free
    if free < need:
        raise DeploymentError(507, "磁盘空间不足，无法复制方案演示视频")


def _copy_preview(src: Path) -> Path:
    """复制演示片到 uploads，basename 冲突则 stem_n.ext。"""
    if not src.is_file():
        raise PackError(f"演示视频不存在: {src.name}")
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


def _cleanup_staged(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理失败应用留下的文件: %s", path)


def _unique_camera_name(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    n = 2
    while f"{base} ({n})" in used:
        n += 1
    return f"{base} ({n})"


def _duration_sec(path: Path) -> float | None:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        if fps > 0 and frames > 0:
            return frames / fps
        return None
    finally:
        cap.release()
