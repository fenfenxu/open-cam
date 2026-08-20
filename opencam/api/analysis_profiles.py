"""分析方案、推理阶段与摄像头绑定 API。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import session_scope
from ..pipeline import start_camera
from ..models import (
    AnalysisProfile,
    AnalysisProfileCreate,
    AnalysisProfileOut,
    AnalysisProfileUpdate,
    Camera,
    CameraBinding,
    CameraBindingCreate,
    CameraBindingOut,
    PipelineStage,
    PipelineStageCreate,
    PipelineStageOut,
    PipelineStageUpdate,
)

router = APIRouter(prefix="/api/analysis-profiles", tags=["analysis-profiles"])
camera_router = APIRouter(prefix="/api/cameras", tags=["analysis-profiles"])


class ProfileCameraBindingCreate(BaseModel):
    camera_id: int
    profile_version: str | None = None
    enabled: bool = True


def _stage_out(stage: PipelineStage) -> PipelineStageOut:
    return PipelineStageOut.model_validate(stage)


def _profile_out(profile: AnalysisProfile) -> AnalysisProfileOut:
    stages = (profile.__dict__.get("stages") or None)
    if stages is None:
        # 不依赖 SQLAlchemy relationship，避免 API 层扩大 ORM 图。
        session = Session.object_session(profile)
        stages = (session.query(PipelineStage)
                  .filter_by(profile_id=profile.id)
                  .order_by(PipelineStage.order_index, PipelineStage.id)
                  .all()) if session is not None else []
    return AnalysisProfileOut(
        id=profile.id,
        key=profile.key,
        name=profile.name,
        description=profile.description,
        version=profile.version,
        input_contract=profile.input_contract or {},
        frame_rate=profile.frame_rate,
        latency_budget_ms=profile.latency_budget_ms,
        status=profile.status,
        solution_pack_id=profile.solution_pack_id,
        metadata=profile.metadata_json or {},
        stages=[_stage_out(stage) for stage in stages],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _get_profile(profile_id: int, session: Session) -> AnalysisProfile:
    profile = session.get(AnalysisProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "分析方案不存在")
    return profile


def _get_camera(camera_id: int, session: Session) -> Camera:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    return camera


def _add_stages(profile: AnalysisProfile, stages: list[PipelineStageCreate],
                session: Session) -> None:
    keys: set[str] = set()
    for item in stages:
        if item.key in keys or session.query(PipelineStage).filter_by(
                profile_id=profile.id, key=item.key).first() is not None:
            raise HTTPException(409, f"方案阶段 key 重复: {item.key}")
        keys.add(item.key)
        session.add(PipelineStage(
            profile_id=profile.id,
            key=item.key,
            name=item.name or item.key,
            order_index=item.order_index,
            capabilities=list(item.capabilities),
            input_contract=dict(item.input_contract),
            output_contract=dict(item.output_contract),
            model_slot_key=item.model_slot_key,
            model_version_id=item.model_version_id,
            created_at=time.time(),
            updated_at=time.time(),
        ))


@router.get("", response_model=list[AnalysisProfileOut], summary="分析方案列表")
def list_profiles(status: str | None = None,
                  solution_pack_id: str | None = None,
                  session: Session = Depends(session_scope)):
    query = session.query(AnalysisProfile).order_by(
        AnalysisProfile.updated_at.desc(), AnalysisProfile.id.desc())
    if status:
        query = query.filter_by(status=status)
    if solution_pack_id:
        query = query.filter_by(solution_pack_id=solution_pack_id)
    return [_profile_out(profile) for profile in query.all()]


@router.post("", response_model=AnalysisProfileOut, status_code=201,
             summary="创建分析方案")
def create_profile(body: AnalysisProfileCreate,
                   session: Session = Depends(session_scope)):
    if session.query(AnalysisProfile).filter_by(key=body.key).first() is not None:
        raise HTTPException(409, "分析方案 key 已存在")
    now = time.time()
    profile = AnalysisProfile(
        key=body.key,
        name=body.name,
        description=body.description,
        version=body.version,
        input_contract=dict(body.input_contract),
        frame_rate=body.frame_rate,
        latency_budget_ms=body.latency_budget_ms,
        status=body.status,
        solution_pack_id=body.solution_pack_id,
        metadata_json=dict(body.metadata),
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    session.flush()
    _add_stages(profile, body.stages, session)
    session.commit()
    session.refresh(profile)
    return _profile_out(profile)


@router.get("/{profile_id}", response_model=AnalysisProfileOut,
            summary="分析方案详情")
def get_profile(profile_id: int, session: Session = Depends(session_scope)):
    return _profile_out(_get_profile(profile_id, session))


@router.patch("/{profile_id}", response_model=AnalysisProfileOut,
              summary="更新分析方案")
@router.put("/{profile_id}", response_model=AnalysisProfileOut,
            include_in_schema=False)
def update_profile(profile_id: int, body: AnalysisProfileUpdate,
                   session: Session = Depends(session_scope)):
    profile = _get_profile(profile_id, session)
    for field in (
        "name", "description", "version", "input_contract", "frame_rate",
        "latency_budget_ms", "status",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(profile, field, value)
    if body.metadata is not None:
        profile.metadata_json = body.metadata
    profile.updated_at = time.time()
    running_camera_ids = [binding.camera_id for binding in session.query(CameraBinding)
                          .filter_by(analysis_profile_id=profile.id).all()
                          if _get_camera(binding.camera_id, session).status == "running"]
    session.commit()
    for camera_id in running_camera_ids:
        start_camera(camera_id)
    session.refresh(profile)
    return _profile_out(profile)


@router.get("/{profile_id}/stages", response_model=list[PipelineStageOut],
            summary="分析方案阶段列表")
def list_stages(profile_id: int, session: Session = Depends(session_scope)):
    _get_profile(profile_id, session)
    return [_stage_out(stage) for stage in session.query(PipelineStage).filter_by(
        profile_id=profile_id).order_by(PipelineStage.order_index, PipelineStage.id).all()]


@router.post("/{profile_id}/stages", response_model=PipelineStageOut,
             status_code=201, summary="添加分析阶段")
def create_stage(profile_id: int, body: PipelineStageCreate,
                 session: Session = Depends(session_scope)):
    profile = _get_profile(profile_id, session)
    _add_stages(profile, [body], session)
    session.commit()
    stage = session.query(PipelineStage).filter_by(
        profile_id=profile_id, key=body.key).one()
    return _stage_out(stage)


@router.patch("/{profile_id}/stages/{stage_id}", response_model=PipelineStageOut,
              summary="更新分析阶段")
def update_stage(profile_id: int, stage_id: int, body: PipelineStageUpdate,
                 session: Session = Depends(session_scope)):
    """更新阶段模型槽位/版本；摄像头下次运行循环前会重启加载新计划。"""
    profile = _get_profile(profile_id, session)
    stage = session.get(PipelineStage, stage_id)
    if stage is None or stage.profile_id != profile.id:
        raise HTTPException(404, "分析阶段不存在")
    if body.model_version_id is not None:
        from ..models import ModelVersion
        if session.get(ModelVersion, body.model_version_id) is None:
            raise HTTPException(404, "模型版本不存在")
    for field in ("name", "order_index", "capabilities", "input_contract",
                  "output_contract", "model_slot_key", "model_version_id"):
        value = getattr(body, field)
        if value is not None:
            setattr(stage, field, value)
    stage.updated_at = time.time()
    camera_ids = [binding.camera_id for binding in session.query(CameraBinding)
                  .filter_by(analysis_profile_id=profile.id).all()]
    running_camera_ids = [camera_id for camera_id in camera_ids
                          if _get_camera(camera_id, session).status == "running"]
    session.commit()
    for camera_id in running_camera_ids:
        start_camera(camera_id)
    session.refresh(stage)
    return _stage_out(stage)


@router.get("/{profile_id}/camera-bindings", response_model=list[CameraBindingOut],
            summary="方案绑定的摄像头")
def profile_camera_bindings(profile_id: int, session: Session = Depends(session_scope)):
    _get_profile(profile_id, session)
    return [_binding_out(binding, session) for binding in session.query(CameraBinding)
            .filter_by(analysis_profile_id=profile_id).order_by(CameraBinding.id).all()]


@router.post("/{profile_id}/camera-bindings", response_model=CameraBindingOut,
             status_code=201, summary="将方案绑定到摄像头")
def bind_profile_camera(profile_id: int, body: ProfileCameraBindingCreate,
                        session: Session = Depends(session_scope)):
    profile = _get_profile(profile_id, session)
    camera = _get_camera(body.camera_id, session)
    if profile.status == "archived":
        raise HTTPException(409, "归档方案不能绑定摄像头")
    binding = session.query(CameraBinding).filter_by(camera_id=camera.id).first()
    if binding is not None and binding.analysis_profile_id != profile.id:
        binding.analysis_profile_id = profile.id
        binding.profile_version = body.profile_version or profile.version
        binding.enabled = body.enabled
        binding.updated_at = time.time()
    elif binding is not None:
        binding.profile_version = body.profile_version or profile.version
        binding.enabled = body.enabled
        binding.updated_at = time.time()
    elif binding is None:
        binding = CameraBinding(
            camera_id=camera.id,
            analysis_profile_id=profile.id,
            profile_version=body.profile_version or profile.version,
            enabled=body.enabled,
            created_at=time.time(),
            updated_at=time.time(),
        )
        session.add(binding)
    was_running = camera.status == "running"
    session.commit()
    if was_running:
        # 方案绑定是运行时输入；正在运行的摄像头必须重启以获取新快照。
        start_camera(camera.id)
    session.refresh(binding)
    return _binding_out(binding, session)


def _binding_out(binding: CameraBinding, session: Session) -> CameraBindingOut:
    profile = session.get(AnalysisProfile, binding.analysis_profile_id)
    return CameraBindingOut(
        id=binding.id,
        camera_id=binding.camera_id,
        analysis_profile_id=binding.analysis_profile_id,
        profile_version=binding.profile_version,
        enabled=binding.enabled,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
        analysis_profile=_profile_out(profile) if profile is not None else None,
    )


@camera_router.get("/{camera_id}/analysis-profile", response_model=CameraBindingOut | None,
                   summary="查看摄像头分析方案")
def get_camera_profile(camera_id: int, session: Session = Depends(session_scope)):
    _get_camera(camera_id, session)
    binding = session.query(CameraBinding).filter_by(camera_id=camera_id).first()
    return _binding_out(binding, session) if binding is not None else None


@camera_router.put("/{camera_id}/analysis-profile", response_model=CameraBindingOut,
                   summary="绑定或替换摄像头分析方案")
@camera_router.post("/{camera_id}/analysis-profile", response_model=CameraBindingOut,
                    status_code=201, include_in_schema=False)
def set_camera_profile(camera_id: int, body: CameraBindingCreate,
                       session: Session = Depends(session_scope)):
    camera = _get_camera(camera_id, session)
    profile = _get_profile(body.analysis_profile_id, session)
    if profile.status == "archived":
        raise HTTPException(409, "归档方案不能绑定摄像头")
    binding = session.query(CameraBinding).filter_by(camera_id=camera_id).first()
    now = time.time()
    if binding is None:
        binding = CameraBinding(
            camera_id=camera_id,
            analysis_profile_id=profile.id,
            profile_version=body.profile_version or profile.version,
            enabled=body.enabled,
            created_at=now,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.analysis_profile_id = profile.id
        binding.profile_version = body.profile_version or profile.version
        binding.enabled = body.enabled
        binding.updated_at = now
    was_running = camera.status == "running"
    session.commit()
    if was_running:
        start_camera(camera.id)
    session.refresh(binding)
    return _binding_out(binding, session)


@camera_router.delete("/{camera_id}/analysis-profile", status_code=204,
                      summary="解除摄像头分析方案")
def delete_camera_profile(camera_id: int, session: Session = Depends(session_scope)):
    camera = _get_camera(camera_id, session)
    binding = session.query(CameraBinding).filter_by(camera_id=camera_id).first()
    if binding is not None:
        session.delete(binding)
        was_running = camera.status == "running"
        session.commit()
        if was_running:
            # 删除绑定后恢复旧版默认检测器，保持摄像头可用。
            start_camera(camera.id)
    return Response(status_code=204)
