"""规则管理 API：按摄像头的规则 CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import RULE_TYPE_NAMES, Camera, Rule, RuleCreate, RuleOut

router = APIRouter(prefix="/cameras/{camera_id}/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut], summary="摄像头的规则列表")
def list_rules(camera_id: int, session: Session = Depends(session_scope)):
    return session.query(Rule).filter_by(camera_id=camera_id).order_by(Rule.id).all()


@router.post("", response_model=RuleOut, status_code=201, summary="创建规则", description="name 不传时用规则类型中文名兜底；params 结构随类型而定（见 /api/rules/presets）。")
def create_rule(camera_id: int, body: RuleCreate,
                session: Session = Depends(session_scope)):
    if session.get(Camera, camera_id) is None:
        raise HTTPException(404, "摄像头不存在")
    rule = Rule(camera_id=camera_id, name=body.name or RULE_TYPE_NAMES[body.type],
                type=body.type, params=body.params,
                enabled=body.enabled, cooldown=body.cooldown)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleOut, summary="更新规则")
def update_rule(camera_id: int, rule_id: int, body: RuleCreate,
                session: Session = Depends(session_scope)):
    rule = session.get(Rule, rule_id)
    if rule is None or rule.camera_id != camera_id:
        raise HTTPException(404, "规则不存在")
    rule.name = body.name or RULE_TYPE_NAMES[body.type]
    rule.type = body.type
    rule.params = body.params
    rule.enabled = body.enabled
    rule.cooldown = body.cooldown
    session.commit()
    session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204, summary="删除规则")
def delete_rule(camera_id: int, rule_id: int,
                session: Session = Depends(session_scope)):
    rule = session.get(Rule, rule_id)
    if rule is None or rule.camera_id != camera_id:
        raise HTTPException(404, "规则不存在")
    session.delete(rule)
    session.commit()
    return Response(status_code=204)
