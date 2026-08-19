"""模型版本登记、A/B 对比、部署与回滚。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opencam.db import init_db
from opencam.training.registry import compare_metrics
from opencam.training.storage import task_dir


@pytest.fixture()
def client(tmp_settings):
    init_db(tmp_settings.db_url)
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _bin_def():
    return {
        "object": "垃圾桶",
        "property": "满溢状态",
        "classes": ["空/正常", "将满", "满溢"],
        "rule": {"type": "state_alert", "trigger": "满溢 持续 5 分钟"},
        "metrics": {
            "accuracy": 0.90,
            "recall": 0.85,
            "false_alarm_per_day": 2,
        },
    }


def _confirm_task(client, task_id="bin1"):
    resp = client.post("/training/tasks", json={
        "goal": "垃圾桶快满了就提醒我",
        "confirm": True,
        "task_id": task_id,
        "definition": _bin_def(),
    })
    assert resp.status_code == 200
    return task_id


def _touch_artifact(task_id, name="best.pt"):
    root = task_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"fake-weights")
    return path


def _register(client, task_id, metrics, artifact_name="best.pt"):
    path = _touch_artifact(task_id, artifact_name)
    resp = client.post("/models", json={
        "task_id": task_id,
        "metrics": metrics,
        "artifact_path": str(path),
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_compare_metrics_recommends_only_when_all_better():
    live = {"accuracy": 0.90, "recall": 0.85, "false_alarm_per_day": 2}
    better = {"accuracy": 0.93, "recall": 0.90, "false_alarm_per_day": 1}
    mixed = {"accuracy": 0.99, "recall": 0.80, "false_alarm_per_day": 0}
    tied = dict(live)
    assert compare_metrics(better, live)["recommend_replace"] is True
    assert compare_metrics(mixed, live)["recommend_replace"] is False
    assert compare_metrics(tied, live)["recommend_replace"] is False
    assert compare_metrics(better, None)["recommend_replace"] is True


def test_register_and_first_deploy(client):
    task_id = _confirm_task(client)
    row = _register(client, task_id, {
        "accuracy": 0.91, "recall": 0.86, "false_alarm_per_day": 1,
    })
    assert row["status"] == "registered"
    assert row["slot_key"] == "垃圾桶:满溢状态"
    assert row["task_id"] == task_id

    listed = client.get("/models").json()
    assert len(listed) == 1

    deployed = client.post(f"/models/{row['id']}/deploy", json={}).json()
    assert deployed["deployed"] is True
    assert deployed["recommend_replace"] is True
    assert deployed["model"]["status"] == "live"
    assert deployed["previous_id"] is None


def test_worse_model_rejected_unless_forced(client):
    task_id = _confirm_task(client)
    live = _register(client, task_id, {
        "accuracy": 0.92, "recall": 0.88, "false_alarm_per_day": 1,
    }, "v1.pt")
    client.post(f"/models/{live['id']}/deploy", json={})

    worse = _register(client, task_id, {
        "accuracy": 0.80, "recall": 0.70, "false_alarm_per_day": 5,
    }, "v2.pt")
    cmp = client.get(f"/models/{worse['id']}/compare").json()
    assert cmp["recommend_replace"] is False

    refused = client.post(f"/models/{worse['id']}/deploy", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["recommend_replace"] is False

    forced = client.post(f"/models/{worse['id']}/deploy", json={"force": True})
    assert forced.status_code == 200
    assert forced.json()["force"] is True
    assert forced.json()["model"]["status"] == "live"
    assert forced.json()["previous_id"] == live["id"]


def test_rollback_restores_previous(client):
    task_id = _confirm_task(client)
    v1 = _register(client, task_id, {
        "accuracy": 0.90, "recall": 0.85, "false_alarm_per_day": 2,
    }, "v1.pt")
    client.post(f"/models/{v1['id']}/deploy", json={})
    v2 = _register(client, task_id, {
        "accuracy": 0.95, "recall": 0.90, "false_alarm_per_day": 1,
    }, "v2.pt")
    client.post(f"/models/{v2['id']}/deploy", json={})

    rolled = client.post(f"/models/{v2['id']}/rollback", json={})
    assert rolled.status_code == 200
    body = rolled.json()
    assert body["rolled_back"] is True
    assert body["model"]["id"] == v1["id"]
    assert body["model"]["status"] == "live"
    assert client.get(f"/models/{v2['id']}").json()["status"] == "previous"

    only = _confirm_task(client, "bin2")
    # 覆盖定义，换一个槽位，避免与 bin1 共用线上 previous
    from opencam.training.storage import save_definition, load_definition
    defn = load_definition("bin2")
    defn["object"] = "工服"
    defn["property"] = "着装合规"
    save_definition("bin2", defn)
    lonely = _register(client, only, {
        "accuracy": 0.9, "recall": 0.85, "false_alarm_per_day": 2,
    })
    client.post(f"/models/{lonely['id']}/deploy", json={})
    empty = client.post(f"/models/{lonely['id']}/rollback")
    assert empty.status_code == 400


def test_register_requires_artifact_and_task(client):
    missing = client.post("/models", json={
        "task_id": "nope",
        "metrics": {"accuracy": 0.9, "recall": 0.8, "false_alarm_per_day": 1},
    })
    assert missing.status_code == 404

    task_id = _confirm_task(client)
    no_file = client.post("/models", json={
        "task_id": task_id,
        "metrics": {"accuracy": 0.9, "recall": 0.8, "false_alarm_per_day": 1},
    })
    assert no_file.status_code == 400


def test_register_reads_eval_json(client):
    task_id = _confirm_task(client)
    _touch_artifact(task_id)
    eval_path = task_dir(task_id) / "eval.json"
    eval_path.write_text(
        '{"accuracy": 0.94, "recall": 0.91, "false_alarm_per_day": 0}',
        encoding="utf-8")
    resp = client.post("/models", json={"task_id": task_id})
    assert resp.status_code == 200
    assert resp.json()["metrics"]["accuracy"] == pytest.approx(0.94)


def test_openapi_includes_model_version_paths(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "post" in paths["/models"]
    assert "get" in paths["/models"]
    assert "post" in paths["/models/{model_id}/deploy"]
    assert "post" in paths["/models/{model_id}/rollback"]
