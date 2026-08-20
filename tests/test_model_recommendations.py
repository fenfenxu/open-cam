"""Stage 4：模型关联推荐与人工审核边界。"""

from __future__ import annotations

from opencam.db import get_session, init_db
from opencam.models import AnalysisProfile, ModelAsset, ModelVersion, PipelineStage


def _fixture(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    artifact = tmp_path / "person.pt"
    artifact.write_bytes(b"recommendation-model")
    session = get_session()
    profile = AnalysisProfile(
        key="recommend-profile", name="门口人员分析",
        input_contract={"modality": "video_frame", "format": "bgr"},
    )
    session.add(profile)
    session.flush()
    stage = PipelineStage(
        profile_id=profile.id, key="detect", name="人员检测",
        capabilities=["person.box"],
        input_contract={"modality": "video_frame", "format": "bgr"},
        output_contract={"person.box": "bbox"},
    )
    session.add(stage)
    asset = ModelAsset(
        name="门口人员检测模型", description="识别人和行人框",
        origin_type="uploaded", distribution_type="private",
        model_kind="object_detection", capabilities=["person.box"],
        input_contract={"modality": "video_frame", "format": "bgr"},
        output_contract={"person.box": "bbox"}, status="active",
        source_type="uploaded",
    )
    session.add(asset)
    session.flush()
    version = ModelVersion(
        task_id="recommend-version", model_asset_id=asset.id, slot_key="person",
        artifact_path=str(artifact), artifact_hash="", metrics={}, status="registered",
    )
    session.add(version)
    session.commit()
    session.close()
    return profile.id, stage.id, asset.id, version.id


def test_recommendation_is_explainable_pending_and_idempotent(tmp_settings, tmp_path):
    _, stage_id, asset_id, version_id = _fixture(tmp_settings, tmp_path)
    from fastapi.testclient import TestClient
    from opencam.main import app

    with TestClient(app) as client:
        response = client.post("/api/model-bindings/recommend", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "model_asset_ids": [asset_id],
        })
        assert response.status_code == 201, response.text
        body = response.json()
        assert len(body) == 1
        recommendation = body[0]
        assert recommendation["model_asset_id"] == asset_id
        assert recommendation["model_version_id"] == version_id
        assert recommendation["relation_source"] == "ai_recommended"
        assert recommendation["relation_status"] == "pending"
        assert recommendation["enabled"] is False
        assert recommendation["confidence"] > 0.5
        assert recommendation["reason"]
        assert "尚未上线" in "；".join(recommendation["warnings"])

        again = client.post("/api/model-bindings/recommend", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "model_asset_ids": [asset_id],
        })
        assert again.status_code == 201
        assert [item["id"] for item in again.json()] == [recommendation["id"]]

        confirmed = client.post(
            f"/api/model-bindings/{recommendation['id']}/confirm", json={})
        assert confirmed.status_code == 200
        assert confirmed.json()["relation_status"] == "confirmed"
        assert confirmed.json()["enabled"] is True

        # 确认关系本身不把版本写入阶段，也不触发部署。
        profile = client.get(f"/api/analysis-profiles/{_}/stages")
        assert profile.status_code == 200
        assert profile.json()[0]["model_version_id"] is None


def test_manual_binding_blocks_ai_recommendation(tmp_settings, tmp_path):
    _, stage_id, asset_id, _ = _fixture(tmp_settings, tmp_path)
    from fastapi.testclient import TestClient
    from opencam.main import app

    with TestClient(app) as client:
        manual = client.post(f"/api/models/assets/{asset_id}/bindings", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "relation_source": "manual",
        })
        assert manual.status_code == 201, manual.text
        recommendations = client.post("/api/model-bindings/recommend", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "model_asset_ids": [asset_id],
        })
        assert recommendations.status_code == 201
        assert recommendations.json() == []


def test_rejected_recommendation_is_not_recreated(tmp_settings, tmp_path):
    _, stage_id, asset_id, _ = _fixture(tmp_settings, tmp_path)
    from fastapi.testclient import TestClient
    from opencam.main import app

    with TestClient(app) as client:
        recommendation = client.post("/api/model-bindings/recommend", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "model_asset_ids": [asset_id],
        }).json()[0]
        rejected = client.post(
            f"/api/model-bindings/{recommendation['id']}/reject",
            json={"reason": "人工选择其他模型"},
        )
        assert rejected.status_code == 200
        again = client.post("/api/model-bindings/recommend", json={
            "target_type": "pipeline_stage", "target_id": stage_id,
            "model_asset_ids": [asset_id],
        })
        assert again.status_code == 201
        assert again.json() == []
