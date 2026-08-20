"""模型资产管理与模型关联 API。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from opencam.db import init_db


def test_system_builtin_model_is_visible_in_model_manager(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        builtins = client.get("/api/models/assets?source_type=builtin").json()
        assert any(
            row["name"] == "YOLOv8 Nano（系统内置）"
            and row["task_key"] == "person_detection"
            for row in builtins
        )


def test_model_asset_crud_keeps_source_type_and_description(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        created = client.post("/api/models/assets", json={
            "name": "门口客流模型",
            "description": "识别人和跟踪客流，适合门店门口机位。",
            "source_type": "solution",
            "model_kind": "object_detection",
            "task_key": "person_detection",
            "solution_pack_id": "fast-food",
        })
        assert created.status_code == 201, created.text
        asset = created.json()
        assert asset["name"] == "门口客流模型"
        assert asset["description"].startswith("识别人")
        assert asset["source_type"] == "solution"
        assert asset["model_kind"] == "object_detection"
        assert asset["solution_pack_id"] == "fast-food"

        updated = client.patch(f"/api/models/assets/{asset['id']}", json={
            "description": "更新后的门口客流模型说明",
        })
        assert updated.status_code == 200
        assert updated.json()["description"] == "更新后的门口客流模型说明"

        listed = client.get("/api/models/assets?source_type=solution").json()
        assert [row["id"] for row in listed] == [asset["id"]]


def test_model_asset_can_bind_to_rule_manually_or_as_ai_recommendation(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        camera = client.post("/api/cameras", json={
            "name": "门口",
            "source_type": "file",
            "source_uri": "/tmp/door.mp4",
        }).json()
        rule = client.post(f"/api/cameras/{camera['id']}/rules", json={
            "name": "门口人数统计",
            "type": "object_count",
            "params": {"class": "person", "threshold": 3},
        }).json()
        asset = client.post("/api/models/assets", json={
            "name": "门店人员检测",
            "description": "用于门店人员检测",
            "source_type": "builtin",
            "model_kind": "object_detection",
            "task_key": "person_detection",
        }).json()

        manual = client.post(f"/api/models/assets/{asset['id']}/bindings", json={
            "target_type": "rule",
            "target_id": rule["id"],
            "relation_source": "manual",
        })
        assert manual.status_code == 201, manual.text
        assert manual.json()["target_type"] == "rule"
        assert manual.json()["relation_source"] == "manual"

        recommendation = client.post(
            f"/api/models/assets/{asset['id']}/bindings", json={
                "target_type": "camera",
                "target_id": camera["id"],
                "relation_source": "ai_recommended",
                "confidence": 0.86,
                "reason": "模型描述包含人员检测，摄像头规则使用 person 类别。",
            })
        assert recommendation.status_code == 201, recommendation.text
        assert recommendation.json()["relation_source"] == "ai_recommended"
        assert recommendation.json()["confidence"] == 0.86

        bindings = client.get(f"/api/models/assets/{asset['id']}/bindings").json()
        assert len(bindings) == 2


def test_model_asset_binding_validates_target_and_duplicate(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        asset = client.post("/api/models/assets", json={
            "name": "测试模型",
            "description": "测试",
            "source_type": "uploaded",
            "model_kind": "classification",
        }).json()

        missing_target = client.post(
            f"/api/models/assets/{asset['id']}/bindings", json={
                "target_type": "rule",
                "relation_source": "manual",
            })
        assert missing_target.status_code == 400

        unknown_target_type = client.post(
            f"/api/models/assets/{asset['id']}/bindings", json={
                "target_type": "unknown",
                "target_key": "x",
                "relation_source": "manual",
            })
        assert unknown_target_type.status_code == 422


def test_trained_model_version_gets_a_model_asset(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app
    from opencam.training.storage import task_dir

    with TestClient(app) as client:
        task_id = "model-asset-task"
        confirmed = client.post("/api/training/tasks", json={
            "goal": "垃圾桶快满了就提醒我",
            "confirm": True,
            "task_id": task_id,
            "definition": {
                "object": "垃圾桶",
                "property": "满溢状态",
                "classes": ["正常", "满溢"],
            },
        })
        assert confirmed.status_code == 200, confirmed.text
        artifact = task_dir(task_id) / "best.pt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"fake-weights")

        registered = client.post("/api/models", json={
            "task_id": task_id,
            "metrics": {"accuracy": 0.9, "recall": 0.8,
                         "false_alarm_per_day": 1},
        })
        assert registered.status_code == 200, registered.text
        version = registered.json()
        assert version["model_asset_id"] is not None

        asset = client.get(
            f"/api/models/assets/{version['model_asset_id']}"
        ).json()
        assert asset["source_type"] == "trained"
        assert asset["training_task_id"] == task_id
