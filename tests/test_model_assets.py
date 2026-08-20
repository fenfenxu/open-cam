"""模型资产管理与模型关联 API。"""

from __future__ import annotations

import hashlib
import io

from fastapi.testclient import TestClient

from opencam.db import init_db


def test_system_builtin_model_is_visible_in_model_manager(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        builtins = client.get("/api/models/assets?origin_type=builtin").json()
        assert any(
            row["name"] == "YOLOv8 Nano（系统内置）"
            and row["task_key"] == "person_detection"
            and row["distribution_type"] == "private"
            and "person_detection" in row["capabilities"]
            for row in builtins
        )


def test_model_asset_crud_keeps_origin_distribution_and_description(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        created = client.post("/api/models/assets", json={
            "name": "门口客流模型",
            "description": "识别人和跟踪客流，适合门店门口机位。",
            "origin_type": "uploaded",
            "distribution_type": "solution",
            "model_kind": "object_detection",
            "capabilities": ["person_detection", "person.box"],
            "task_key": "person_detection",
            "solution_pack_id": "fast-food",
        })
        assert created.status_code == 201, created.text
        asset = created.json()
        assert asset["name"] == "门口客流模型"
        assert asset["description"].startswith("识别人")
        assert asset["origin_type"] == "uploaded"
        assert asset["distribution_type"] == "solution"
        assert asset["model_kind"] == "object_detection"
        assert asset["capabilities"] == ["person_detection", "person.box"]
        assert asset["solution_pack_id"] == "fast-food"

        updated = client.patch(f"/api/models/assets/{asset['id']}", json={
            "description": "更新后的门口客流模型说明",
            "distribution_type": "published",
        })
        assert updated.status_code == 200
        assert updated.json()["description"] == "更新后的门口客流模型说明"
        assert updated.json()["distribution_type"] == "published"

        listed = client.get(
            "/api/models/assets?distribution_type=published").json()
        assert [row["id"] for row in listed] == [asset["id"]]


def test_model_asset_list_filters_by_origin_kind_capability_and_text(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        target = client.post("/api/models/assets", json={
            "name": "工服识别模型",
            "description": "识别员工是否穿工服",
            "origin_type": "trained",
            "model_kind": "classification",
            "capabilities": ["uniform_classification"],
        })
        assert target.status_code == 201, target.text
        target_id = target.json()["id"]
        client.post("/api/models/assets", json={
            "name": "车牌识别模型",
            "description": "识别车牌文字",
            "origin_type": "uploaded",
            "model_kind": "ocr",
            "capabilities": ["plate_ocr"],
        })

        by_origin = client.get("/api/models/assets?origin_type=trained").json()
        assert [row["id"] for row in by_origin] == [target_id]

        by_kind = client.get("/api/models/assets?model_kind=classification").json()
        assert [row["id"] for row in by_kind] == [target_id]

        by_capability = client.get(
            "/api/models/assets?capability=uniform_classification").json()
        assert [row["id"] for row in by_capability] == [target_id]

        by_text = client.get("/api/models/assets?q=工服").json()
        assert [row["id"] for row in by_text] == [target_id]


def test_model_asset_can_be_archived_and_restored(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as client:
        asset = client.post("/api/models/assets", json={
            "name": "旧模型",
            "origin_type": "uploaded",
            "model_kind": "object_detection",
        }).json()

        archived = client.patch(f"/api/models/assets/{asset['id']}", json={
            "status": "archived",
        })
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

        active = client.get("/api/models/assets?status=active").json()
        assert all(row["id"] != asset["id"] for row in active)

        restored = client.patch(f"/api/models/assets/{asset['id']}", json={
            "status": "active",
        })
        assert restored.status_code == 200
        assert restored.json()["status"] == "active"


def test_model_upload_registers_traceable_asset_and_version(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    payload = b"fake-uploaded-weights"
    with TestClient(app) as client:
        resp = client.post(
            "/api/models/assets/upload",
            files={"file": ("helmet.pt", io.BytesIO(payload),
                            "application/octet-stream")},
            data={
                "name": "安全帽检测模型",
                "description": "检测人员是否佩戴安全帽",
                "model_kind": "object_detection",
                "capabilities": "helmet_detection, helmet.box",
                "framework": "yolov8",
                "runtime": "ultralytics",
                "input_size": "640",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        asset, version = body["asset"], body["version"]
        assert asset["origin_type"] == "uploaded"
        assert asset["distribution_type"] == "private"
        assert asset["capabilities"] == ["helmet_detection", "helmet.box"]
        assert version["model_asset_id"] == asset["id"]
        assert version["artifact_hash"] == hashlib.sha256(payload).hexdigest()
        assert version["framework"] == "yolov8"
        assert version["runtime"] == "ultralytics"
        assert version["input_size"] == 640

        listed = client.get(
            "/api/models/assets?capability=helmet_detection").json()
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
            "origin_type": "builtin",
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
            "origin_type": "uploaded",
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
        assert version["artifact_hash"] == hashlib.sha256(b"fake-weights").hexdigest()
        assert version["framework"] == "yolov8"
        assert version["runtime"] == "ultralytics"

        asset = client.get(
            f"/api/models/assets/{version['model_asset_id']}"
        ).json()
        assert asset["origin_type"] == "trained"
        assert asset["distribution_type"] == "private"
        assert asset["training_task_id"] == task_id


def test_pack_install_registers_declared_models(tmp_settings, tmp_path):
    """方案包 models 声明在安装后变成可追溯资产；重复安装幂等。"""
    init_db(tmp_settings.db_url)
    from opencam.main import app

    pack = tmp_path / "model-pack"
    (pack / "rules").mkdir(parents=True)
    (pack / "models").mkdir()
    (pack / "pack.yaml").write_text(
        "id: model-pack\n"
        "name: 模型包\n"
        "version: 1.0.0\n"
        "vertical: test\n"
        "models:\n"
        "  - id: uniform-detector\n"
        "    name: 工服检测模型\n"
        "    description: 随方案交付的工服检测\n"
        "    model_kind: object_detection\n"
        "    capabilities: [uniform_classification]\n"
        "    file: models/uniform.pt\n"
        "    framework: yolov8\n"
        "    runtime: ultralytics\n",
        encoding="utf-8")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 测试规则\ntype: object_count\nparams:\n  class: person\n",
        encoding="utf-8")
    weights = b"pack-weights"
    (pack / "models" / "uniform.pt").write_bytes(weights)

    with TestClient(app) as client:
        installed = client.post("/api/packs/install", json={"source": str(pack)})
        assert installed.status_code == 201, installed.text

        assets = client.get(
            "/api/models/assets?distribution_type=solution").json()
        assert len(assets) == 1
        asset = assets[0]
        assert asset["name"] == "工服检测模型"
        assert asset["origin_type"] == "builtin"
        assert asset["solution_pack_id"] == "model-pack"
        assert asset["capabilities"] == ["uniform_classification"]

        versions = client.get("/api/models", params={
            "task_id": "pack-model-pack-uniform-detector"}).json()
        assert len(versions) == 1
        assert versions[0]["artifact_hash"] == hashlib.sha256(weights).hexdigest()
        assert versions[0]["model_asset_id"] == asset["id"]

        # 重复安装不重复登记
        again = client.post("/api/packs/install", json={"source": str(pack)})
        assert again.status_code == 201, again.text
        assets = client.get(
            "/api/models/assets?distribution_type=solution").json()
        assert len(assets) == 1


def test_pack_install_rejects_missing_model_file(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    pack = tmp_path / "broken-model-pack"
    (pack / "rules").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        "id: broken-model-pack\n"
        "name: 缺权重的包\n"
        "version: 1.0.0\n"
        "vertical: test\n"
        "models:\n"
        "  - id: ghost\n"
        "    name: 幽灵模型\n"
        "    file: models/ghost.pt\n",
        encoding="utf-8")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 测试规则\ntype: object_count\nparams:\n  class: person\n",
        encoding="utf-8")

    with TestClient(app) as client:
        resp = client.post("/api/packs/install", json={"source": str(pack)})
        assert resp.status_code == 400
        assert "权重文件不存在" in resp.json()["detail"]
