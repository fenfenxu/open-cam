"""训练模型版本 API：登记、列表、部署前 A/B 对比、部署与回滚。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import ModelVersion, ModelVersionOut
from ..training.registry import (
    RegistryError,
    comparison_for,
    deploy_version,
    list_versions,
    register_version,
    rollback_slot,
)

router = APIRouter(prefix="/models", tags=["models"])


class RegisterModel(BaseModel):
    task_id: str = Field(description="来源训练任务 id")
    metrics: Optional[dict[str, Any]] = Field(
        None, description="accuracy / recall / false_alarm_per_day；缺省读 eval.json")
    artifact_path: Optional[str] = Field(
        None, description="产物路径；缺省 data/training/<task_id>/best.pt")


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


@router.post("", response_model=ModelVersionOut, summary="登记模型版本",
             description="记录指标、产物路径与来源任务；不改变线上模型。")
def register(body: RegisterModel, session: Session = Depends(session_scope)):
    try:
        return register_version(
            session, body.task_id,
            metrics=body.metrics, artifact_path=body.artifact_path)
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
