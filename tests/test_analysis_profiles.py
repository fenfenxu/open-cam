"""分析方案、能力化规则与模型关联审核。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from opencam.db import init_db


def test_profile_stage_camera_binding_and_capability_rule(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        profile = client.post("/api/analysis-profiles", json={
            "key": "store-front",
            "name": "门店门口分析",
            "input_contract": {"modality": "video_frame", "format": "bgr"},
            "stages": [{
                "key": "person-detection",
                "name": "人员检测",
                "capabilities": ["person.box"],
                "output_contract": {"person.box": "bbox"},
            }],
        })
        assert profile.status_code == 201, profile.text
        profile_body = profile.json()
        assert profile_body["stages"][0]["capabilities"] == ["person.box"]

        camera = client.post("/api/cameras", json={
            "name": "门口",
            "source_type": "file",
            "source_uri": "/tmp/door.mp4",
        }).json()
        bound = client.put(
            f"/api/cameras/{camera['id']}/analysis-profile",
            json={"analysis_profile_id": profile_body["id"]},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["profile_version"] == "1"

        rule = client.post(f"/api/cameras/{camera['id']}/rules", json={
            "name": "人数",
            "type": "object_count",
            "params": {"class": "person", "threshold": 2},
        })
        assert rule.status_code == 201, rule.text
        assert rule.json()["capabilities"] == ["person.box"]


def test_ai_model_binding_requires_review(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        profile = client.post("/api/analysis-profiles", json={
            "key": "review-profile", "name": "审核方案",
            "stages": [{"key": "detect", "capabilities": ["person.box"]}],
        }).json()
        stage = profile["stages"][0]
        asset = client.post("/api/models/assets", json={
            "name": "人员模型",
            "model_kind": "object_detection",
            "capabilities": ["person.box"],
        }).json()
        recommendation = client.post(
            f"/api/models/assets/{asset['id']}/bindings", json={
                "target_type": "pipeline_stage",
                "target_id": stage["id"],
                "relation_source": "ai_recommended",
                "confidence": 0.91,
                "reason": "能力契约匹配",
            })
        assert recommendation.status_code == 201, recommendation.text
        binding = recommendation.json()
        assert binding["relation_status"] == "pending"
        assert binding["enabled"] is False

        confirmed = client.post(
            f"/api/model-bindings/{binding['id']}/confirm", json={})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["relation_status"] == "confirmed"
        assert confirmed.json()["enabled"] is True

        rejected = client.post(
            f"/api/model-bindings/{binding['id']}/reject",
            json={"reason": "设备不支持"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["relation_status"] == "rejected"
        assert rejected.json()["enabled"] is False
