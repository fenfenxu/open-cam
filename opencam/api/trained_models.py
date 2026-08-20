"""训练模型版本 API：登记、列表、部署前 A/B 对比、部署与回滚。"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    Camera,
    ModelAsset,
    ModelAssetCreate,
    ModelAssetOut,
    ModelAssetUpdate,
    ModelBinding,
    ModelBindingCreate,
    ModelBindingOut,
    ModelVersion,
    ModelVersionOut,
    Rule,
)
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

router = APIRouter(prefix="/api/models", tags=["models"])


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
    source_type: str = Field(
        "trained", pattern="^(builtin|published|solution|uploaded|trained)$")
    model_kind: str = Field(
        "classification",
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")


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


def _validate_binding_target(body: ModelBindingCreate, session: Session) -> None:
    uses_id = body.target_type in {"rule", "camera"}
    if uses_id and body.target_id is None:
        raise HTTPException(400, f"{body.target_type} 关联必须提供 target_id")
    if not uses_id and not body.target_key:
        raise HTTPException(400, f"{body.target_type} 关联必须提供 target_key")
    if body.target_type == "rule" and session.get(Rule, body.target_id) is None:
        raise HTTPException(404, "规则不存在")
    if body.target_type == "camera" and session.get(Camera, body.target_id) is None:
        raise HTTPException(404, "摄像头不存在")


@router.get("/assets", response_model=list[ModelAssetOut], summary="模型资产列表")
def list_assets(
    source_type: Optional[str] = Query(None),
    model_kind: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="按名称或描述搜索"),
    session: Session = Depends(session_scope),
):
    query = session.query(ModelAsset).order_by(ModelAsset.updated_at.desc(), ModelAsset.id.desc())
    if source_type:
        query = query.filter(ModelAsset.source_type == source_type)
    if model_kind:
        query = query.filter(ModelAsset.model_kind == model_kind)
    if q:
        query = query.filter(or_(ModelAsset.name.contains(q), ModelAsset.description.contains(q)))
    return query.all()


@router.post("/assets", response_model=ModelAssetOut, status_code=201,
             summary="创建模型资产",
             description="登记模型名称、描述、来源类型和模型能力；不等于部署到运行时。")
def create_asset(body: ModelAssetCreate, session: Session = Depends(session_scope)):
    now = time.time()
    asset = ModelAsset(
        name=body.name,
        description=body.description,
        source_type=body.source_type,
        model_kind=body.model_kind,
        task_key=body.task_key,
        solution_pack_id=body.solution_pack_id,
        training_task_id=body.training_task_id,
        metadata_json=body.metadata,
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


@router.get("/assets/{asset_id}", response_model=ModelAssetOut, summary="模型资产详情")
def get_asset(asset_id: int, session: Session = Depends(session_scope)):
    return _get_asset_or_404(asset_id, session)


@router.patch("/assets/{asset_id}", response_model=ModelAssetOut, summary="更新模型资产")
def update_asset(asset_id: int, body: ModelAssetUpdate,
                 session: Session = Depends(session_scope)):
    asset = _get_asset_or_404(asset_id, session)
    for field in (
        "name", "description", "source_type", "model_kind", "task_key",
        "solution_pack_id", "training_task_id", "status",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(asset, field, value)
    if body.metadata is not None:
        asset.metadata_json = body.metadata
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
    _validate_binding_target(body, session)
    duplicate_query = session.query(ModelBinding).filter_by(
        model_asset_id=asset_id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
    )
    if duplicate_query.first() is not None:
        raise HTTPException(409, "该模型与目标已经存在关联")
    binding = ModelBinding(
        model_asset_id=asset_id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_key=body.target_key,
        relation_source=body.relation_source,
        confidence=body.confidence,
        reason=body.reason,
        enabled=body.enabled,
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
                source_type=body.source_type,
                model_kind=body.model_kind,
                task_key=slot_key,
                training_task_id=body.task_id,
                metadata_json={"classes": definition.get("classes") or []},
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(asset)
            session.flush()
            asset_id = asset.id
        return register_version(
            session, body.task_id,
            metrics=body.metrics, artifact_path=body.artifact_path,
            model_asset_id=asset_id)
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
        return deploy_version(session, model_id, force=body.force)
    except RegistryError as exc:
        _raise(exc)


@router.post("/{model_id}/rollback", response_model=DeployResult,
             summary="回滚到上一线上版本",
             description="按该版本所在槽位恢复 previous；没有上一版本时 400。")
def rollback(model_id: int, session: Session = Depends(session_scope)):
    try:
        return rollback_slot(session, model_id)
    except RegistryError as exc:
        _raise(exc)


@router.get("/{model_id}/compare", summary="与线上模型对比（不部署）")
def compare(model_id: int, session: Session = Depends(session_scope)):
    row = session.get(ModelVersion, model_id)
    if row is None:
        raise HTTPException(404, "模型版本不存在")
    return comparison_for(session, row)
