"""摄像头运行时方案解析。

运行时只在摄像头启动时解析一次，并把结果冻结在 ``RuntimePlan`` 中。
运行中的流水线不会每帧重新查询模型表；模型或方案发生变化后，下一次
启动（由调用方 stop/start 或 reconnect）会生成新的计划。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from .hardware import resolve_device
from .models import (
    AnalysisProfile,
    Camera,
    CameraBinding,
    MODEL_BINDING_CONFIRMED,
    MODEL_LIVE,
    ModelAsset,
    ModelBinding,
    ModelVersion,
    PipelineStage,
)


class RuntimeResolutionError(RuntimeError):
    """模型或分析方案不满足运行约束时抛出，信息可直接展示给用户。"""

    status_code = "runtime_model_unavailable"


@dataclass(frozen=True)
class RuntimeStage:
    stage_id: int
    key: str
    capabilities: tuple[str, ...]
    model_version_id: Optional[int]
    artifact_path: Optional[str]
    artifact_digest: Optional[str]
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    latency_ms: Optional[float]


@dataclass(frozen=True)
class RuntimePlan:
    """摄像头一次启动对应的不可变运行时计划。"""

    camera_id: int
    status: str
    runtime_status: str
    reason: Optional[str]
    analysis_profile_id: Optional[int]
    analysis_profile_version: Optional[str]
    device: str
    created_at: float
    stages: tuple[RuntimeStage, ...] = ()

    @property
    def model_path(self) -> Optional[str]:
        """返回当前检测器使用的首个目标检测产物。"""
        for stage in self.stages:
            if stage.artifact_path:
                return stage.artifact_path
        return None

    @property
    def primary_stage(self) -> Optional[RuntimeStage]:
        return self.stages[0] if self.stages else None

    def stage_for(self, capabilities: list[str] | tuple[str, ...]) -> Optional[RuntimeStage]:
        """按规则需要的能力选择实际产生该事件的阶段。"""
        required = set(capabilities)
        for stage in self.stages:
            if required and required.issubset(set(stage.capabilities)):
                return stage
        return self.primary_stage

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["stages"] = [asdict(stage) for stage in self.stages]
        return value


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_matches(required: dict[str, Any], provided: dict[str, Any]) -> bool:
    """检查阶段要求是否被模型契约覆盖；空模型契约表示旧模型兼容。"""
    if not provided:
        return True
    return all(provided.get(key) == value for key, value in required.items())


def _device_compatible(asset: ModelAsset, version: ModelVersion, device: str) -> bool:
    metadata = dict(asset.metadata_json or {})
    metrics = dict(version.metrics or {})
    declared = metrics.get("device") or metadata.get("device")
    declared_many = metrics.get("devices") or metadata.get("devices")
    if declared_many:
        values = declared_many if isinstance(declared_many, list) else [declared_many]
        return device in {str(item).lower() for item in values}
    if declared:
        return str(declared).lower() == device.lower()
    # runtime 通常是 ultralytics/onnxruntime，不是设备名；只有明确写成
    # cpu/cuda/mps 时才按设备约束处理。
    runtime = (version.runtime or "").lower()
    return runtime not in {"cpu", "cuda", "mps"} or runtime == device.lower()


def _compatible(version: ModelVersion, asset: ModelAsset, stage: PipelineStage,
                profile: AnalysisProfile, device: str) -> tuple[bool, str, Optional[float]]:
    capabilities = set(asset.capabilities or [])
    capabilities.update(str(value) for value in (asset.output_contract or {}).values())
    required = set(stage.capabilities or [])
    missing = sorted(required - capabilities)
    if missing:
        return False, f"模型缺少阶段能力: {', '.join(missing)}", None
    required_input = dict(stage.input_contract or profile.input_contract or {})
    if not _contract_matches(required_input, dict(asset.input_contract or {})):
        return False, "模型输入契约与分析方案不兼容", None
    required_size = (required_input.get("size") or required_input.get("width")
                     or required_input.get("input_size"))
    if required_size is not None and version.input_size is not None \
            and int(required_size) != int(version.input_size):
        return False, (f"模型输入尺寸 {version.input_size} 与方案要求 "
                       f"{required_size} 不兼容"), None
    if not _contract_matches(dict(stage.output_contract or {}),
                             dict(asset.output_contract or {})):
        return False, "模型输出契约与分析阶段不兼容", None
    if not _device_compatible(asset, version, device):
        return False, f"模型不支持设备 {device}", None
    metrics = dict(version.metrics or {})
    latency = metrics.get("latency_ms", metrics.get("inference_latency_ms"))
    if profile.latency_budget_ms is not None:
        if latency is None:
            return False, "模型没有可用于延迟预算校验的 latency_ms", None
        if float(latency) > float(profile.latency_budget_ms):
            return False, (f"模型延迟 {float(latency):g}ms 超出方案预算 "
                           f"{float(profile.latency_budget_ms):g}ms"), float(latency)
    return True, "", float(latency) if latency is not None else None


def _candidate_versions(session: Session, stage: PipelineStage,
                        profile: AnalysisProfile) -> list[tuple[ModelVersion, ModelAsset]]:
    """按固定版本、阶段关联、方案关联、槽位的优先级生成候选。"""
    binding_rows = (session.query(ModelBinding)
                    .filter(ModelBinding.enabled.is_(True),
                            ModelBinding.relation_status == MODEL_BINDING_CONFIRMED)
                    .filter(
                        ((ModelBinding.target_type == "pipeline_stage") &
                         (ModelBinding.target_id == stage.id)) |
                        ((ModelBinding.target_type == "analysis_profile") &
                         (ModelBinding.target_id == profile.id)))
                    .all())
    # 人工关系永远排在 AI 推荐之前；确认推荐不会悄悄覆盖人工选择。
    binding_rows.sort(key=lambda row: (
        0 if row.relation_source == "manual" else 1,
        0 if row.target_type == "pipeline_stage" else 1,
        -row.id,
    ))
    binding_asset_ids = [row.model_asset_id for row in binding_rows]

    query = session.query(ModelVersion, ModelAsset).join(
        ModelAsset, ModelAsset.id == ModelVersion.model_asset_id
    ).filter(ModelVersion.status == MODEL_LIVE, ModelAsset.status == "active")
    if stage.model_version_id is not None:
        query = query.filter(ModelVersion.id == stage.model_version_id)
    elif binding_asset_ids:
        query = query.filter(ModelVersion.model_asset_id.in_(binding_asset_ids))
    elif stage.model_slot_key:
        query = query.filter(ModelVersion.slot_key == stage.model_slot_key)
    else:
        # 没有固定槽位时只考虑线上版本，随后由能力/契约校验筛选。
        pass
    rows = query.order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).all()
    if binding_asset_ids:
        rank = {asset_id: index for index, asset_id in enumerate(binding_asset_ids)}
        rows.sort(key=lambda pair: (rank.get(pair[0].model_asset_id, 999),
                                    -pair[0].created_at, -pair[0].id))
    return rows


def _resolve_stage(session: Session, stage: PipelineStage,
                   profile: AnalysisProfile, device: str) -> RuntimeStage:
    candidates = _candidate_versions(session, stage, profile)
    if not candidates:
        target = (f"model_version_id={stage.model_version_id}"
                  if stage.model_version_id is not None
                  else stage.model_slot_key or stage.key)
        raise RuntimeResolutionError(
            f"阶段“{stage.name}”没有可用的线上模型（目标 {target}）")
    reasons: list[str] = []
    for version, asset in candidates:
        ok, reason, latency = _compatible(version, asset, stage, profile, device)
        if not ok:
            reasons.append(reason)
            continue
        path = Path(version.artifact_path).expanduser()
        if not path.is_file():
            reasons.append(f"模型产物不存在: {version.artifact_path}")
            continue
        digest = _digest(path)
        if version.artifact_hash and version.artifact_hash != digest:
            reasons.append(f"模型产物哈希不匹配: {version.id}")
            continue
        return RuntimeStage(
            stage_id=stage.id,
            key=stage.key,
            capabilities=tuple(stage.capabilities or ()),
            model_version_id=version.id,
            artifact_path=str(path),
            artifact_digest=digest,
            input_contract=dict(stage.input_contract or profile.input_contract or {}),
            output_contract=dict(stage.output_contract or {}),
            latency_ms=latency,
        )
    detail = "；".join(dict.fromkeys(reasons))
    raise RuntimeResolutionError(f"阶段“{stage.name}”没有兼容模型: {detail}")


def resolve_runtime_plan(session: Session, camera_id: int,
                         device: Optional[str] = None) -> RuntimePlan:
    """为摄像头解析一次性运行计划；未配置方案的旧摄像头保留兼容模式。"""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise RuntimeResolutionError(f"摄像头不存在: {camera_id}")
    actual_device = resolve_device(device or "auto")
    binding = session.query(CameraBinding).filter_by(camera_id=camera_id).first()
    if binding is None or not binding.enabled:
        return RuntimePlan(
            camera_id=camera_id, status="ready", runtime_status="not_configured",
            reason="未绑定分析方案，使用系统默认检测器", analysis_profile_id=None,
            analysis_profile_version=None, device=actual_device, created_at=time.time())
    profile = session.get(AnalysisProfile, binding.analysis_profile_id)
    if profile is None:
        raise RuntimeResolutionError("摄像头绑定的分析方案不存在")
    if profile.status != "active":
        raise RuntimeResolutionError(f"分析方案“{profile.name}”当前不可运行（状态: {profile.status}）")
    stages = (session.query(PipelineStage).filter_by(profile_id=profile.id)
              .order_by(PipelineStage.order_index, PipelineStage.id).all())
    if not stages:
        raise RuntimeResolutionError(f"分析方案“{profile.name}”没有配置推理阶段")
    resolved = tuple(_resolve_stage(session, stage, profile, actual_device)
                     for stage in stages)
    return RuntimePlan(
        camera_id=camera_id, status="ready", runtime_status="ready", reason=None,
        analysis_profile_id=profile.id,
        analysis_profile_version=binding.profile_version or profile.version,
        device=actual_device, created_at=time.time(), stages=resolved)
