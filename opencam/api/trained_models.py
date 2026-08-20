"""训练模型版本 API：登记、列表、部署前 A/B 对比、部署与回滚。

同一路由下也承载模型资产管理：资产 CRUD / 上传 / 关联。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..model_assets import register_uploaded_asset
from ..models import (
    MODEL_BINDING_CONFIRMED,
    MODEL_BINDING_PENDING,
    MODEL_BINDING_REJECTED,
    MODEL_DISTRIBUTION_PRIVATE,
    MODEL_ORIGIN_TRAINED,
    Camera,
    AnalysisProfile,
    ModelAsset,
    ModelAssetCreate,
    ModelAssetOut,
    ModelAssetUpdate,
    ModelBinding,
    ModelBindingCreate,
    ModelBindingOut,
    ModelVersion,
    ModelVersionOut,
    PipelineStage,
    Rule,
    legacy_source_type,
)
from ..model_recommender import recommend_bindings
from ..training.registry import (
    RegistryError,
    comparison_for,
    deploy_version,
    list_versions,
    register_version,
    rollback_slot,
    slot_key_from_definition,
)
from ..training.storage import load_definition, task_exists
from ..pipeline import restart_cameras_for_model_change

router = APIRouter(prefix="/api/models", tags=["models"])
bindings_router = APIRouter(prefix="/api/model-bindings", tags=["models"])


class RegisterModel(BaseModel):
    task_id: str = Field(description="来源训练任务 id")
    metrics: Optional[dict[str, Any]] = Field(
        None, description="accuracy / recall / false_alarm_per_day；缺省读 eval.json")
    artifact_path: Optional[str] = Field(
        None, description="产物路径；缺省 data/training/<task_id>/best.pt")
    model_asset_id: Optional[int] = Field(
        None, description="已有模型资产 id；不传则按训练任务自动创建“自己训练出来的模型”资产")
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    origin_type: str = Field(
        MODEL_ORIGIN_TRAINED, pattern="^(builtin|uploaded|trained)$")
    distribution_type: str = Field(
        MODEL_DISTRIBUTION_PRIVATE, pattern="^(private|published|solution)$")
    model_kind: str = Field(
        "classification",
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")
    capabilities: list[str] = Field(default_factory=list)
    framework: Optional[str] = Field("yolov8", description="训练/导出框架")
    runtime: Optional[str] = Field("ultralytics", description="推理运行时")
    input_size: Optional[int] = Field(None, description="推理输入边长，如 640")


class DeployBody(BaseModel):
    force: bool = Field(
        False, description="未全面更优时仍强制替换；默认拒绝以免训出更差模型上线")


class DeployResult(BaseModel):
    deployed: bool
    already_live: bool = False
    rolled_back: bool = False
    force: bool = False
    recommend_replace: bool
    reason: str
    metrics: dict[str, Any]
    candidate_id: int
    live_id: Optional[int] = None
    slot_key: str
    previous_id: Optional[int] = None
    model: ModelVersionOut


class BindingReview(BaseModel):
    reason: Optional[str] = None
    enabled: Optional[bool] = None


class RecommendationRequest(BaseModel):
    """推荐一个目标的模型资产；只产生待审核关联，不改变运行时配置。"""

    target_type: str = Field(
        pattern="^(rule|camera|analysis_profile|pipeline_stage|solution_pack)$")
    target_id: Optional[int] = None
    target_key: Optional[str] = Field(default=None, max_length=128)
    limit: int = Field(default=5, ge=1, le=20)
    model_asset_ids: Optional[list[int]] = None


def _raise(exc: RegistryError) -> None:
    detail: Any = str(exc)
    if exc.payload:
        detail = {"message": str(exc), **exc.payload}
    raise HTTPException(exc.status_code, detail)


def _get_asset_or_404(asset_id: int, session: Session) -> ModelAsset:
    asset = session.get(ModelAsset, asset_id)
    if asset is None:
        raise HTTPException(404, "模型资产不存在")
    return asset


def _validate_binding_target(body: ModelBindingCreate, session: Session,
                             asset_id: int | None = None) -> None:
    uses_id = body.target_type in {"rule", "camera", "analysis_profile", "pipeline_stage"}
    if uses_id and body.target_id is None and not body.target_key:
        raise HTTPException(400, f"{body.target_type} 关联必须提供 target_id 或 target_key")
    if not uses_id and not body.target_key:
        raise HTTPException(400, f"{body.target_type} 关联必须提供 target_key")
    if body.target_type == "rule" and session.get(Rule, body.target_id) is None:
        raise HTTPException(404, "规则不存在")
    if body.target_type == "camera" and session.get(Camera, body.target_id) is None:
        raise HTTPException(404, "摄像头不存在")
    if body.target_type == "analysis_profile" and body.target_id is not None \
            and session.get(AnalysisProfile, body.target_id) is None:
        raise HTTPException(404, "分析方案不存在")
    if body.target_type == "pipeline_stage" and body.target_id is not None \
            and session.get(PipelineStage, body.target_id) is None:
        raise HTTPException(404, "分析阶段不存在")
    if body.model_version_id is not None:
        version = session.get(ModelVersion, body.model_version_id)
        if version is None:
            raise HTTPException(404, "模型版本不存在")
        if version.model_asset_id is not None and version.model_asset_id != asset_id:
            raise HTTPException(400, "模型版本不属于当前模型资产")


@router.get("/assets", response_model=list[ModelAssetOut], summary="模型资产列表")
def list_assets(
    origin_type: Optional[str] = Query(None, description="产生方式：builtin/uploaded/trained"),
    distribution_type: Optional[str] = Query(None, description="交付方式：private/published/solution"),
    model_kind: Optional[str] = Query(None),
    capability: Optional[str] = Query(None, description="按能力标签过滤"),
    status: Optional[str] = Query(None, description="active/archived；缺省返回全部"),
    q: Optional[str] = Query(None, description="按名称或描述搜索"),
    session: Session = Depends(session_scope),
):
    query = session.query(ModelAsset).order_by(ModelAsset.updated_at.desc(), ModelAsset.id.desc())
    if origin_type:
        query = query.filter(ModelAsset.origin_type == origin_type)
    if distribution_type:
        query = query.filter(ModelAsset.distribution_type == distribution_type)
    if model_kind:
        query = query.filter(ModelAsset.model_kind == model_kind)
    if capability:
        # SQLite 的 JSON 数组按文本匹配，能力标签是完整 token，误命中可忽略
        query = query.filter(ModelAsset.capabilities.contains(f'"{capability}"'))
    if status:
        query = query.filter(ModelAsset.status == status)
    if q:
        query = query.filter(or_(ModelAsset.name.contains(q), ModelAsset.description.contains(q)))
    return query.all()


@router.post("/assets", response_model=ModelAssetOut, status_code=201,
             summary="创建模型资产",
             description="登记模型名称、描述、来源、交付方式和能力；不等于部署到运行时。")
def create_asset(body: ModelAssetCreate, session: Session = Depends(session_scope)):
    now = time.time()
    asset = ModelAsset(
        name=body.name,
        description=body.description,
        origin_type=body.origin_type,
        distribution_type=body.distribution_type,
        model_kind=body.model_kind,
        capabilities=body.capabilities,
        input_contract=body.input_contract,
        output_contract=body.output_contract,
        task_key=body.task_key,
        solution_pack_id=body.solution_pack_id,
        training_task_id=body.training_task_id,
        metadata_json=body.metadata,
        source_type=legacy_source_type(body.origin_type, body.distribution_type),
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


class UploadResult(BaseModel):
    asset: ModelAssetOut
    version: ModelVersionOut


@router.post("/assets/upload", response_model=UploadResult, status_code=201,
             summary="上传模型产物",
             description="上传权重文件并登记为可追溯资产 + 首个版本（含 sha256）。")
def upload_asset(
    file: UploadFile = File(...),
    name: str = Form(min_length=1, max_length=128),
    description: str = Form(""),
    model_kind: str = Form(
        "object_detection",
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$"),
    distribution_type: str = Form(
        MODEL_DISTRIBUTION_PRIVATE, pattern="^(private|published|solution)$"),
    capabilities: str = Form("", description="逗号分隔的能力标签"),
    task_key: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    runtime: Optional[str] = Form(None),
    input_size: Optional[int] = Form(None),
    session: Session = Depends(session_scope),
):
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(400, "缺少文件名")
    dest_dir = settings.data_dir / "models" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)
    dest = dest_dir / f"{int(time.time())}-{safe_name}"
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            out.write(chunk)
    capability_list = [c.strip() for c in capabilities.split(",") if c.strip()]
    asset, version = register_uploaded_asset(
        session,
        file_path=dest,
        name=name,
        description=description,
        model_kind=model_kind,
        distribution_type=distribution_type,
        capabilities=capability_list,
        task_key=task_key or None,
        framework=framework,
        runtime=runtime,
        input_size=input_size,
    )
    return UploadResult(
        asset=ModelAssetOut.model_validate(asset),
        version=ModelVersionOut.model_validate(version),
    )


@router.get("/assets/{asset_id}", response_model=ModelAssetOut, summary="模型资产详情")
def get_asset(asset_id: int, session: Session = Depends(session_scope)):
    return _get_asset_or_404(asset_id, session)


@router.patch("/assets/{asset_id}", response_model=ModelAssetOut, summary="更新模型资产")
def update_asset(asset_id: int, body: ModelAssetUpdate,
                 session: Session = Depends(session_scope)):
    asset = _get_asset_or_404(asset_id, session)
    for field in (
        "name", "description", "origin_type", "distribution_type", "model_kind",
        "capabilities", "input_contract", "output_contract", "task_key",
        "solution_pack_id", "training_task_id", "status",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(asset, field, value)
    if body.metadata is not None:
        asset.metadata_json = body.metadata
    if body.origin_type is not None or body.distribution_type is not None:
        asset.source_type = legacy_source_type(
            asset.origin_type, asset.distribution_type)
    asset.updated_at = time.time()
    session.commit()
    session.refresh(asset)
    return asset


@router.get("/assets/{asset_id}/bindings", response_model=list[ModelBindingOut],
            summary="查看模型关联")
def list_bindings(asset_id: int, session: Session = Depends(session_scope)):
    _get_asset_or_404(asset_id, session)
    return (session.query(ModelBinding)
            .filter_by(model_asset_id=asset_id)
            .order_by(ModelBinding.id)
            .all())


@router.post("/assets/{asset_id}/bindings", response_model=ModelBindingOut,
             status_code=201, summary="关联模型",
             description="支持手工关联，也支持保存 AI 推荐关联的置信度和理由；当前不自动改变运行时流水线。")
def create_binding(asset_id: int, body: ModelBindingCreate,
                   session: Session = Depends(session_scope)):
    _get_asset_or_404(asset_id, session)
    _validate_binding_target(body, session, asset_id)
    duplicate_query = session.query(ModelBinding).filter_by(
        model_asset_id=asset_id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
    )
    if duplicate_query.filter(
            ModelBinding.relation_status != MODEL_BINDING_REJECTED).first() is not None:
        raise HTTPException(409, "该模型与目标已经存在关联")
    if body.relation_source == "ai_recommended":
        if body.confidence is None or not body.reason:
            raise HTTPException(400, "AI 推荐关联必须提供 confidence 和 reason")
        manual_exists = (session.query(ModelBinding)
                         .filter_by(target_type=body.target_type,
                                    target_id=body.target_id,
                                    target_key=body.target_key,
                                    relation_source="manual")
                         .filter(ModelBinding.relation_status != MODEL_BINDING_REJECTED)
                         .first())
        if manual_exists is not None:
            raise HTTPException(409, "目标已有人工关联，不能被 AI 推荐覆盖")
    binding = ModelBinding(
        model_asset_id=asset_id,
        model_version_id=body.model_version_id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
        relation_source=body.relation_source,
        relation_status=(MODEL_BINDING_PENDING
                         if body.relation_source == "ai_recommended"
                         else MODEL_BINDING_CONFIRMED),
        confidence=body.confidence,
        reason=body.reason,
        warnings=list(body.warnings),
        enabled=(body.enabled and body.relation_source != "ai_recommended"),
    )
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return binding


@router.delete("/assets/{asset_id}/bindings/{binding_id}", status_code=204,
               summary="解除模型关联")
def delete_binding(asset_id: int, binding_id: int,
                   session: Session = Depends(session_scope)):
    binding = session.get(ModelBinding, binding_id)
    if binding is None or binding.model_asset_id != asset_id:
        raise HTTPException(404, "模型关联不存在")
    session.delete(binding)
    session.commit()
    return Response(status_code=204)


@bindings_router.get("", response_model=list[ModelBindingOut], summary="查看模型关联")
def list_all_bindings(
    target_type: Optional[str] = Query(None),
    relation_source: Optional[str] = Query(None),
    relation_status: Optional[str] = Query(None),
    session: Session = Depends(session_scope),
):
    query = session.query(ModelBinding).order_by(ModelBinding.id)
    if target_type:
        query = query.filter_by(target_type=target_type)
    if relation_source:
        query = query.filter_by(relation_source=relation_source)
    if relation_status:
        query = query.filter_by(relation_status=relation_status)
    return query.all()


def _get_binding(binding_id: int, session: Session) -> ModelBinding:
    binding = session.get(ModelBinding, binding_id)
    if binding is None:
        raise HTTPException(404, "模型关联不存在")
    return binding


def _review_binding(binding: ModelBinding, *, confirmed: bool,
                    body: BindingReview, session: Session) -> ModelBinding:
    if binding.relation_source != "ai_recommended":
        raise HTTPException(409, "只有 AI 推荐关联需要人工确认或拒绝")
    if confirmed:
        manual_exists = (session.query(ModelBinding)
                         .filter(ModelBinding.id != binding.id,
                                 ModelBinding.target_type == binding.target_type,
                                 ModelBinding.target_id == binding.target_id,
                                 ModelBinding.target_key == binding.target_key,
                                 ModelBinding.relation_source == "manual",
                                 ModelBinding.relation_status != MODEL_BINDING_REJECTED)
                         .first())
        if manual_exists is not None:
            raise HTTPException(409, "目标已有人工关联，不能确认 AI 推荐")
    binding.relation_status = (
        MODEL_BINDING_CONFIRMED if confirmed else MODEL_BINDING_REJECTED)
    binding.enabled = bool(confirmed if body.enabled is None else body.enabled)
    if not confirmed:
        binding.enabled = False
    if body.reason is not None:
        binding.reason = body.reason
    session.commit()
    session.refresh(binding)
    return binding


@bindings_router.get("/{binding_id}", response_model=ModelBindingOut,
                     summary="查看模型关联详情")
def get_binding(binding_id: int, session: Session = Depends(session_scope)):
    return _get_binding(binding_id, session)


@bindings_router.post("/{binding_id}/confirm", response_model=ModelBindingOut,
                      summary="确认模型推荐关联")
def confirm_binding(binding_id: int, body: BindingReview = BindingReview(),
                    session: Session = Depends(session_scope)):
    return _review_binding(_get_binding(binding_id, session), confirmed=True,
                           body=body, session=session)


@bindings_router.post("/{binding_id}/reject", response_model=ModelBindingOut,
                      summary="拒绝模型推荐关联")
def reject_binding(binding_id: int, body: BindingReview = BindingReview(),
                   session: Session = Depends(session_scope)):
    return _review_binding(_get_binding(binding_id, session), confirmed=False,
                           body=body, session=session)


@bindings_router.post("/recommend", response_model=list[ModelBindingOut],
                      status_code=201, summary="生成模型关联推荐",
                      description="按目标的能力与输入输出契约生成可解释候选；候选默认 pending，"
                                  "不会覆盖人工关联，也不会自动部署模型。")
def recommend(body: RecommendationRequest,
              session: Session = Depends(session_scope)):
    return recommend_bindings(
        session,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
        limit=body.limit,
        model_asset_ids=body.model_asset_ids,
    )


@router.post("/recommendations", response_model=list[ModelBindingOut],
             status_code=201, summary="生成模型关联推荐", include_in_schema=False)
def recommend_from_models(body: RecommendationRequest,
                          session: Session = Depends(session_scope)):
    return recommend_bindings(
        session,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
        limit=body.limit,
        model_asset_ids=body.model_asset_ids,
    )


@bindings_router.patch("/{binding_id}", response_model=ModelBindingOut,
                       summary="更新模型关联审核状态")
def update_binding(binding_id: int, body: BindingReview,
                   relation_status: Optional[str] = Query(None),
                   session: Session = Depends(session_scope)):
    binding = _get_binding(binding_id, session)
    if relation_status == MODEL_BINDING_CONFIRMED:
        return _review_binding(binding, confirmed=True, body=body, session=session)
    if relation_status == MODEL_BINDING_REJECTED:
        return _review_binding(binding, confirmed=False, body=body, session=session)
    if body.reason is not None:
        binding.reason = body.reason
    if body.enabled is not None:
        if (binding.relation_source == "ai_recommended"
                and binding.relation_status == MODEL_BINDING_PENDING
                and body.enabled):
            raise HTTPException(409, "待审核 AI 推荐必须先通过 confirm 确认")
        binding.enabled = body.enabled
    session.commit()
    session.refresh(binding)
    return binding


@router.post("", response_model=ModelVersionOut, summary="登记模型版本",
             description="记录指标、产物路径与来源任务；不改变线上模型。")
def register(body: RegisterModel, session: Session = Depends(session_scope)):
    try:
        if not task_exists(body.task_id):
            raise RegistryError("训练任务不存在", 404)
        asset_id = body.model_asset_id
        if asset_id is not None:
            _get_asset_or_404(asset_id, session)
        else:
            definition = load_definition(body.task_id)
            slot_key = slot_key_from_definition(definition)
            asset = ModelAsset(
                name=body.name or slot_key,
                description=body.description or str(definition.get("goal") or ""),
                origin_type=body.origin_type,
                distribution_type=body.distribution_type,
                model_kind=body.model_kind,
                capabilities=body.capabilities,
                task_key=slot_key,
                training_task_id=body.task_id,
                metadata_json={"classes": definition.get("classes") or []},
                source_type=legacy_source_type(body.origin_type, body.distribution_type),
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(asset)
            session.flush()
            asset_id = asset.id
        return register_version(
            session, body.task_id,
            metrics=body.metrics, artifact_path=body.artifact_path,
            model_asset_id=asset_id,
            framework=body.framework, runtime=body.runtime,
            input_size=body.input_size)
    except RegistryError as exc:
        _raise(exc)


@router.get("", response_model=list[ModelVersionOut], summary="模型版本列表")
def list_models(
    task_id: Optional[str] = Query(None),
    slot_key: Optional[str] = Query(None),
    session: Session = Depends(session_scope),
):
    return list_versions(session, task_id=task_id, slot_key=slot_key)


@router.get("/{model_id}", response_model=ModelVersionOut, summary="模型版本详情")
def get_model(model_id: int, session: Session = Depends(session_scope)):
    row = session.get(ModelVersion, model_id)
    if row is None:
        raise HTTPException(404, "模型版本不存在")
    return row


@router.post("/{model_id}/deploy", response_model=DeployResult,
             summary="部署模型",
             description="与线上模型做 A/B 对比，仅在三项指标全面更优时建议并执行替换；"
                         "未更优返回 409，force=true 可强行上线。旧 live 降为 previous，回滚入口常驻。")
def deploy(model_id: int, body: DeployBody = DeployBody(),
           session: Session = Depends(session_scope)):
    try:
        result = deploy_version(session, model_id, force=body.force)
        restart_cameras_for_model_change(session, model_id)
        return result
    except RegistryError as exc:
        _raise(exc)


@router.post("/{model_id}/rollback", response_model=DeployResult,
             summary="回滚到上一线上版本",
             description="按该版本所在槽位恢复 previous；没有上一版本时 400。")
def rollback(model_id: int, session: Session = Depends(session_scope)):
    try:
        result = rollback_slot(session, model_id)
        restart_cameras_for_model_change(session, model_id)
        return result
    except RegistryError as exc:
        _raise(exc)


@router.get("/{model_id}/compare", summary="与线上模型对比（不部署）")
def compare(model_id: int, session: Session = Depends(session_scope)):
    row = session.get(ModelVersion, model_id)
    if row is None:
        raise HTTPException(404, "模型版本不存在")
    return comparison_for(session, row)
