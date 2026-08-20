"""基于本地模型元数据的可解释关联推荐。

推荐器故意保持本地、确定性和无副作用：它只读取模型资产/版本与目标的能力契约，
把候选保存为 ``pending`` 关联；人工确认前不会启用关系，也不会部署或修改推理阶段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import (
    AnalysisProfile,
    Camera,
    CameraBinding,
    MODEL_BINDING_PENDING,
    MODEL_BINDING_REJECTED,
    MODEL_KIND_CLASSIFICATION,
    MODEL_KIND_DETECTION,
    MODEL_KIND_OCR,
    ModelAsset,
    ModelBinding,
    ModelVersion,
    PipelineStage,
    Rule,
)


@dataclass
class TargetRequirements:
    target_type: str
    target_id: int | None
    target_key: str | None
    label: str
    capabilities: set[str] = field(default_factory=set)
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScoredCandidate:
    asset: ModelAsset
    version: ModelVersion | None
    confidence: float
    reason: str
    warnings: list[str]


def _target_match(row: ModelBinding, target: TargetRequirements) -> bool:
    return (
        row.target_type == target.target_type
        and row.target_id == target.target_id
        and row.target_key == target.target_key
    )


def _contract_matches(required: dict[str, Any], provided: dict[str, Any]) -> bool:
    return not provided or all(provided.get(key) == value for key, value in required.items())


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_:.+\-]+|[\u4e00-\u9fff]", value.lower()))


def _target_from_db(
    session: Session, *, target_type: str, target_id: int | None, target_key: str | None
) -> TargetRequirements:
    """把规则、阶段、方案或摄像头归一成同一套推荐输入。"""
    target = TargetRequirements(target_type, target_id, target_key, target_type)
    if target_type == "pipeline_stage":
        stage = session.get(PipelineStage, target_id) if target_id is not None else None
        if stage is None:
            raise HTTPException(404, "分析阶段不存在")
        profile = session.get(AnalysisProfile, stage.profile_id)
        target.label = stage.name
        target.capabilities = set(stage.capabilities or [])
        target.input_contract = dict(stage.input_contract or {})
        target.output_contract = dict(stage.output_contract or {})
        target.text = " ".join(
            item for item in (stage.name, stage.key, profile.name if profile else "") if item
        )
        if not target.capabilities:
            target.warnings.append("分析阶段没有声明能力标签")
        return target

    if target_type == "analysis_profile":
        profile = session.get(AnalysisProfile, target_id) if target_id is not None else None
        if profile is None:
            raise HTTPException(404, "分析方案不存在")
        stages = session.query(PipelineStage).filter_by(profile_id=profile.id).all()
        target.label = profile.name
        target.capabilities = {
            capability for stage in stages for capability in (stage.capabilities or [])
        }
        target.input_contract = dict(profile.input_contract or {})
        target.output_contract = {
            key: value for stage in stages for key, value in (stage.output_contract or {}).items()
        }
        target.text = " ".join((profile.name, profile.key, profile.description or ""))
        if not stages:
            target.warnings.append("分析方案没有配置推理阶段")
        return target

    if target_type == "rule":
        rule = session.get(Rule, target_id) if target_id is not None else None
        if rule is None:
            raise HTTPException(404, "规则不存在")
        target.label = rule.name or rule.type
        target.capabilities = set(rule.capabilities or [])
        target.text = " ".join((rule.name or "", rule.type, str(rule.params or {})))
        if not target.capabilities:
            target.warnings.append("规则没有声明能力标签，推荐结果仅依据名称和参数")
        return target

    if target_type == "camera":
        camera = session.get(Camera, target_id) if target_id is not None else None
        if camera is None:
            raise HTTPException(404, "摄像头不存在")
        target.label = camera.name
        target.text = camera.name
        binding = session.query(CameraBinding).filter_by(camera_id=camera.id).first()
        if binding is None:
            target.warnings.append("摄像头尚未绑定分析方案")
        else:
            profile = session.get(AnalysisProfile, binding.analysis_profile_id)
            if profile is not None:
                target.text += " " + " ".join(
                    (profile.name, profile.description or "")
                )
                stages = session.query(PipelineStage).filter_by(profile_id=profile.id).all()
                target.capabilities = {
                    capability for stage in stages for capability in (stage.capabilities or [])
                }
                target.input_contract = dict(profile.input_contract or {})
        rules = session.query(Rule).filter_by(camera_id=camera.id, enabled=True).all()
        target.capabilities.update(
            capability for rule in rules for capability in (rule.capabilities or [])
        )
        target.text += " " + " ".join(
            (rule.name or rule.type) for rule in rules
        )
        if not target.capabilities:
            target.warnings.append("摄像头没有可用于推荐的能力需求")
        return target

    if target_type == "solution_pack":
        if not target_key:
            raise HTTPException(400, "方案包关联必须提供 target_key")
        target.label = target_key
        target.text = target_key
        target.warnings.append("方案包关联使用 target_key，无法从数据库读取阶段契约")
        return target

    raise HTTPException(422, "不支持的推荐目标类型")


def _latest_version(session: Session, asset_id: int) -> ModelVersion | None:
    versions = (session.query(ModelVersion)
                .filter_by(model_asset_id=asset_id)
                .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
                .all())
    live = next((row for row in versions if row.status == "live"), None)
    return live or next((row for row in versions if row.status == "registered"), None)


def _expected_kind(capabilities: set[str]) -> str | None:
    if any(capability.endswith(".box") or capability.endswith("_detection")
           for capability in capabilities):
        return MODEL_KIND_DETECTION
    if any(capability.endswith(".text") or capability.endswith("_ocr")
           for capability in capabilities):
        return MODEL_KIND_OCR
    if any(capability.endswith(".attribute") or capability.endswith("_classification")
           for capability in capabilities):
        return MODEL_KIND_CLASSIFICATION
    return None


def _score(asset: ModelAsset, version: ModelVersion | None,
           target: TargetRequirements) -> ScoredCandidate | None:
    provided = set(asset.capabilities or [])
    provided.update(str(key) for key in (asset.output_contract or {}))
    provided.update(str(value) for value in (asset.output_contract or {}).values())
    required = target.capabilities
    overlap = required & provided
    text_overlap = _tokens(target.text) & _tokens(
        " ".join((asset.name, asset.description or "", asset.task_key or ""))
    )
    task_match = bool(asset.task_key and asset.task_key in target.capabilities)
    if not overlap and not text_overlap and not task_match:
        return None

    warnings = list(target.warnings)
    score = 0.18 if required and not overlap else 0.0
    if required:
        score += 0.52 * len(overlap) / len(required)
        missing = sorted(required - provided)
        if missing:
            warnings.append("模型缺少能力: " + ", ".join(missing))
    if task_match:
        score += 0.16
    if text_overlap:
        score += min(0.14, 0.035 * len(text_overlap))

    if target.input_contract and asset.input_contract \
            and not _contract_matches(target.input_contract, asset.input_contract):
        score -= 0.18
        warnings.append("模型输入契约与目标要求不完全匹配")
    if target.output_contract and asset.output_contract \
            and not _contract_matches(target.output_contract, asset.output_contract):
        score -= 0.18
        warnings.append("模型输出契约与目标要求不完全匹配")

    expected_kind = _expected_kind(required)
    if expected_kind and asset.model_kind != expected_kind:
        score -= 0.12
        warnings.append(f"目标倾向{expected_kind}，模型类型为{asset.model_kind}")
    if version is None:
        warnings.append("模型资产还没有可追踪的模型版本")
    elif version.status != "live":
        warnings.append("模型版本尚未上线；确认关联不会自动部署")

    confidence = round(max(0.05, min(0.99, score)), 3)
    if overlap == required and required:
        reason = "模型能力标签完整覆盖目标需求"
    elif overlap:
        reason = "模型能力标签部分匹配目标需求"
    elif task_match:
        reason = "模型任务标识与目标能力匹配"
    else:
        reason = "模型名称或描述与目标语义相关"
    if text_overlap:
        reason += "，名称/描述存在语义线索"
    return ScoredCandidate(asset, version, confidence, reason, list(dict.fromkeys(warnings)))


def recommend_bindings(
    session: Session, *, target_type: str, target_id: int | None = None,
    target_key: str | None = None, limit: int = 5,
    model_asset_ids: list[int] | None = None,
) -> list[ModelBinding]:
    """生成并保存待审核推荐；已有人工关系时返回空列表且不创建 AI 关系。"""
    target = _target_from_db(
        session, target_type=target_type, target_id=target_id, target_key=target_key)
    existing = session.query(ModelBinding).filter_by(target_type=target_type).all()
    matching = [row for row in existing if _target_match(row, target)]
    if any(row.relation_source == "manual" and row.relation_status != MODEL_BINDING_REJECTED
           for row in matching):
        return []

    query = session.query(ModelAsset).filter_by(status="active")
    if model_asset_ids:
        query = query.filter(ModelAsset.id.in_(model_asset_ids))
    candidates = []
    for asset in query.order_by(ModelAsset.updated_at.desc(), ModelAsset.id.desc()).all():
        candidate = _score(asset, _latest_version(session, asset.id), target)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (-item.confidence, item.asset.id))

    selected: list[ModelBinding] = []
    for candidate in candidates[:limit]:
        same_asset = next(
            (row for row in matching if row.model_asset_id == candidate.asset.id), None)
        if same_asset is not None:
            # 拒绝是人工决策，重复推荐不能把它悄悄复活；pending 可幂等刷新解释信息。
            if same_asset.relation_status == MODEL_BINDING_REJECTED:
                continue
            if same_asset.relation_status == MODEL_BINDING_PENDING:
                same_asset.confidence = candidate.confidence
                same_asset.reason = candidate.reason
                same_asset.warnings = candidate.warnings
                same_asset.model_version_id = candidate.version.id if candidate.version else None
                same_asset.enabled = False
            selected.append(same_asset)
            continue
        binding = ModelBinding(
            model_asset_id=candidate.asset.id,
            model_version_id=candidate.version.id if candidate.version else None,
            target_type=target.target_type,
            target_id=target.target_id,
            target_key=target.target_key,
            relation_source="ai_recommended",
            relation_status=MODEL_BINDING_PENDING,
            confidence=candidate.confidence,
            reason=candidate.reason,
            warnings=candidate.warnings,
            enabled=False,
        )
        session.add(binding)
        selected.append(binding)
    session.commit()
    for binding in selected:
        session.refresh(binding)
    return selected
