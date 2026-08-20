"""Stage 3：摄像头启动时解析兼容模型并记录运行时产物。"""

from __future__ import annotations

import hashlib

import pytest

from opencam.db import get_session, init_db
from opencam.models import (
    AnalysisProfile,
    Camera,
    CameraBinding,
    Event,
    ModelAsset,
    ModelVersion,
    PipelineStage,
)
from opencam.pipeline import persist_hit
from opencam.runtime import RuntimeResolutionError, resolve_runtime_plan
from opencam.detection.rules import RuleHit


def _fixture(tmp_settings, tmp_path, *, latency_budget_ms=None,
             capabilities=None):
    init_db(tmp_settings.db_url)
    artifact = tmp_path / "person.pt"
    artifact.write_bytes(b"runtime-model")
    session = get_session()
    camera = Camera(name="门口", source_type="file", source_uri="/tmp/camera.mp4")
    session.add(camera)
    session.flush()
    profile = AnalysisProfile(
        key="runtime", name="运行方案", version="7",
        input_contract={"modality": "video_frame", "format": "bgr"},
        latency_budget_ms=latency_budget_ms,
    )
    session.add(profile)
    session.flush()
    stage = PipelineStage(
        profile_id=profile.id, key="detect", name="人员检测",
        capabilities=capabilities or ["person.box"],
        input_contract={"modality": "video_frame", "format": "bgr"},
        output_contract={"person.box": "bbox"}, model_slot_key="person",
    )
    session.add(stage)
    asset = ModelAsset(
        name="人员模型", description="", origin_type="uploaded",
        distribution_type="private", model_kind="object_detection",
        capabilities=["person.box"],
        input_contract={"modality": "video_frame", "format": "bgr"},
        output_contract={"person.box": "bbox"}, status="active",
        source_type="uploaded",
    )
    session.add(asset)
    session.flush()
    version = ModelVersion(
        task_id="runtime-task", model_asset_id=asset.id, slot_key="person",
        artifact_path=str(artifact), artifact_hash=hashlib.sha256(
            artifact.read_bytes()).hexdigest(), framework="mock",
        runtime="mock", metrics={"latency_ms": 10}, status="live",
    )
    session.add(version)
    session.flush()
    session.add(CameraBinding(camera_id=camera.id, analysis_profile_id=profile.id,
                              profile_version=profile.version))
    session.commit()
    return session, camera, profile, stage, version


def test_runtime_plan_selects_live_compatible_version(tmp_settings, tmp_path):
    session, camera, profile, stage, version = _fixture(tmp_settings, tmp_path)
    try:
        plan = resolve_runtime_plan(session, camera.id, device="cpu")
        assert plan.status == "ready"
        assert plan.analysis_profile_version == "7"
        assert plan.primary_stage.model_version_id == version.id
        assert plan.primary_stage.artifact_digest == version.artifact_hash
        assert plan.model_path == version.artifact_path
    finally:
        session.close()


def test_runtime_plan_rejects_latency_and_reports_reason(tmp_settings, tmp_path):
    session, camera, *_ = _fixture(tmp_settings, tmp_path, latency_budget_ms=5)
    try:
        with pytest.raises(RuntimeResolutionError, match="延迟"):
            resolve_runtime_plan(session, camera.id, device="cpu")
    finally:
        session.close()


def test_event_records_runtime_model_provenance(tmp_settings, tmp_path):
    session, camera, profile, stage, version = _fixture(tmp_settings, tmp_path)
    try:
        from opencam.models import Rule

        rule = Rule(camera_id=camera.id, type="object_count",
                    params={"class": "person", "threshold": 1})
        session.add(rule)
        session.commit()
        plan = resolve_runtime_plan(session, camera.id, device="cpu")
        event = persist_hit(
            session, camera.id, rule,
            RuleHit(rule_id=rule.id, rule_type=rule.type, confidence=0.9),
            None, runtime_plan=plan)
        assert event is not None
        assert event.analysis_profile_version == profile.version
        assert event.pipeline_stage == stage.key
        assert event.model_version_id == version.id
        assert event.artifact_digest == version.artifact_hash
        stored = session.get(Event, event.id)
        assert stored.artifact_digest == version.artifact_hash
    finally:
        session.close()
