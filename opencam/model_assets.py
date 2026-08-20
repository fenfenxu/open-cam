"""模型资产生命周期的本机初始化与登记逻辑。

内置模型幂等登记、上传产物落库、方案包随包模型登记都在这里；
写入时同步维护过渡字段 source_type（由 origin/distribution 派生）。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .config import settings
from .models import (
    AnalysisProfile,
    MODEL_DISTRIBUTION_PRIVATE,
    MODEL_DISTRIBUTION_SOLUTION,
    MODEL_KIND_DETECTION,
    MODEL_ORIGIN_BUILTIN,
    MODEL_ORIGIN_UPLOADED,
    MODEL_REGISTERED,
    ModelAsset,
    ModelVersion,
    PipelineStage,
    legacy_source_type,
)

_BUILTIN_TASK_KEY = "person_detection"
_BUILTIN_CAPABILITIES = ["person_detection"]
_BUILTIN_INPUT_CONTRACT = {"modality": "video_frame", "format": "bgr"}
_BUILTIN_OUTPUT_CONTRACT = {"person_detection": "person.box"}


def sha256_file(path: Path) -> str:
    """分块计算文件 sha256，供版本登记与上传共用。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_asset(session: Session, *, name: str, description: str,
               origin_type: str, distribution_type: str, model_kind: str,
               capabilities: Optional[list[str]] = None,
               input_contract: Optional[dict] = None,
               output_contract: Optional[dict] = None,
               task_key: Optional[str] = None,
               solution_pack_id: Optional[str] = None,
               training_task_id: Optional[str] = None,
               metadata: Optional[dict] = None) -> ModelAsset:
    now = time.time()
    asset = ModelAsset(
        name=name,
        description=description,
        origin_type=origin_type,
        distribution_type=distribution_type,
        model_kind=model_kind,
        capabilities=list(capabilities or []),
        input_contract=dict(input_contract or {}),
        output_contract=dict(output_contract or {}),
        task_key=task_key,
        solution_pack_id=solution_pack_id,
        training_task_id=training_task_id,
        metadata_json=dict(metadata or {}),
        source_type=legacy_source_type(origin_type, distribution_type),
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    session.flush()
    return asset


def ensure_builtin_assets(session: Session) -> None:
    """幂等登记当前系统自带的基础模型，不覆盖用户编辑过的名称与描述。"""
    exists = (session.query(ModelAsset)
              .filter_by(origin_type=MODEL_ORIGIN_BUILTIN, task_key=_BUILTIN_TASK_KEY)
              .first())
    if exists is not None:
        # 旧库升级上来的记录只补空的能力与契约字段，用户填过的不动
        changed = False
        if not exists.capabilities:
            exists.capabilities = list(_BUILTIN_CAPABILITIES)
            changed = True
        if not exists.input_contract:
            exists.input_contract = dict(_BUILTIN_INPUT_CONTRACT)
            changed = True
        if not exists.output_contract:
            exists.output_contract = dict(_BUILTIN_OUTPUT_CONTRACT)
            changed = True
        if changed:
            exists.updated_at = time.time()
            session.commit()
        return
    _new_asset(
        session,
        name="YOLOv8 Nano（系统内置）",
        description="系统默认目标检测模型，适合人员、车辆等通用目标检测。",
        origin_type=MODEL_ORIGIN_BUILTIN,
        distribution_type=MODEL_DISTRIBUTION_PRIVATE,
        model_kind=MODEL_KIND_DETECTION,
        capabilities=_BUILTIN_CAPABILITIES,
        input_contract=_BUILTIN_INPUT_CONTRACT,
        output_contract=_BUILTIN_OUTPUT_CONTRACT,
        task_key=_BUILTIN_TASK_KEY,
        metadata={"artifact_path": settings.yolo_model},
    )
    session.commit()


def register_uploaded_asset(session: Session, *, file_path: Path, name: str,
                            description: str, model_kind: str,
                            distribution_type: str = MODEL_DISTRIBUTION_PRIVATE,
                            capabilities: Optional[list[str]] = None,
                            task_key: Optional[str] = None,
                            framework: Optional[str] = None,
                            runtime: Optional[str] = None,
                            input_size: Optional[int] = None,
                            ) -> tuple[ModelAsset, ModelVersion]:
    """把已落在数据目录内的上传产物登记为资产 + 首个版本。"""
    asset = _new_asset(
        session,
        name=name,
        description=description,
        origin_type=MODEL_ORIGIN_UPLOADED,
        distribution_type=distribution_type,
        model_kind=model_kind,
        capabilities=capabilities,
        task_key=task_key,
    )
    version = ModelVersion(
        task_id=f"upload-{asset.id}",
        model_asset_id=asset.id,
        slot_key=task_key or f"asset:{asset.id}",
        artifact_path=str(file_path),
        artifact_hash=sha256_file(file_path),
        framework=framework or None,
        runtime=runtime or None,
        input_size=input_size,
        metrics={},
        created_at=time.time(),
        status=MODEL_REGISTERED,
    )
    session.add(version)
    session.commit()
    session.refresh(asset)
    session.refresh(version)
    return asset, version


def register_pack_models(session: Session, pack_dir: Path, pack_id: str,
                         ) -> list[ModelAsset]:
    """方案安装后登记包内声明的模型资产；按包内模型 id 幂等，不覆盖用户编辑。"""
    from .packs.manifest import load_manifest  # 延迟 import 避免循环

    manifest = load_manifest(pack_dir)
    declared = manifest.models or []
    if not declared:
        return []
    existing = (session.query(ModelAsset)
                .filter_by(solution_pack_id=pack_id)
                .all())
    by_pack_model_id = {
        (asset.metadata_json or {}).get("pack_model_id"): asset
        for asset in existing
    }
    created: list[ModelAsset] = []
    for entry in declared:
        if entry.id in by_pack_model_id:
            continue
        asset = _new_asset(
            session,
            name=entry.name,
            description=entry.description,
            origin_type=MODEL_ORIGIN_BUILTIN,
            distribution_type=MODEL_DISTRIBUTION_SOLUTION,
            model_kind=entry.model_kind,
            capabilities=entry.capabilities,
            solution_pack_id=pack_id,
            metadata={"pack_model_id": entry.id, "pack_file": entry.file},
        )
        if entry.file:
            artifact = (pack_dir / entry.file).resolve()
            version = ModelVersion(
                task_id=f"pack-{pack_id}-{entry.id}",
                model_asset_id=asset.id,
                slot_key=f"pack-{pack_id}-{entry.id}",
                artifact_path=str(artifact),
                artifact_hash=sha256_file(artifact),
                framework=entry.framework or None,
                runtime=entry.runtime or None,
                metrics={},
                created_at=time.time(),
                status=MODEL_REGISTERED,
            )
            session.add(version)
        created.append(asset)
    session.commit()
    return created


def register_pack_profiles(session: Session, pack_dir: Path, pack_id: str,
                           ) -> list[AnalysisProfile]:
    """登记方案包声明的分析方案和能力阶段，按包内 profile key 幂等。"""
    from .packs.manifest import load_manifest  # 延迟 import 避免循环

    manifest = load_manifest(pack_dir)
    declared = manifest.analysis_profiles or []
    if not declared:
        return []
    created: list[AnalysisProfile] = []
    for entry in declared:
        profile_key = entry.key
        existing = (session.query(AnalysisProfile)
                    .filter_by(solution_pack_id=pack_id, key=profile_key)
                    .first())
        if existing is not None:
            continue
        # key 在本机是稳定引用；发生跨包冲突时保留原包 key 并加包前缀。
        if session.query(AnalysisProfile).filter_by(key=profile_key).first() is not None:
            profile_key = f"{pack_id}:{entry.key}"
        now = time.time()
        profile = AnalysisProfile(
            key=profile_key,
            name=entry.name,
            description=entry.description,
            version=entry.version,
            input_contract=dict(entry.input_contract),
            frame_rate=entry.frame_rate,
            latency_budget_ms=entry.latency_budget_ms,
            status="active",
            solution_pack_id=pack_id,
            metadata_json={"pack_profile_key": entry.key},
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
        for stage in entry.stages:
            session.add(PipelineStage(
                profile_id=profile.id,
                key=stage.key,
                name=stage.name or stage.key,
                order_index=stage.order_index,
                capabilities=list(stage.capabilities),
                input_contract=dict(stage.input_contract),
                output_contract=dict(stage.output_contract),
                model_slot_key=stage.model_slot_key,
                created_at=now,
                updated_at=now,
            ))
        created.append(profile)
    session.commit()
    return created
