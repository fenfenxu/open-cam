"""PackDeployment 测试：变更计划、原子应用、指纹 409、部署状态与校准启用。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from opencam.db import get_session, init_db
from opencam.models import (
    Camera,
    PackDeployment,
    PackDeploymentResource,
    Rule,
    Video,
)
from opencam.packs import deployment as deploy
from opencam.packs.manifest import PackError

W, H = 320, 240


def _make_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (W, H))
    assert writer.isOpened()
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    for _ in range(5):
        writer.write(frame)
    writer.release()


def _client():
    from fastapi.testclient import TestClient
    from opencam.main import app

    return TestClient(app)


def _add_camera(session, tmp_path) -> Camera:
    video = tmp_path / "cam.mp4"
    _make_video(video)
    camera = Camera(name="门口", source_type="file", source_uri=str(video))
    session.add(camera)
    session.commit()
    return camera


# ---------- 变更计划 ----------


def test_plan_new_pack_no_writes(tmp_settings):
    """新包计划：4 机位 4 规则，含指纹与后续步骤；不产生任何 DB 写入。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        plan = deploy.plan("fast-food", session)
        assert plan.mode == "create_cameras"
        assert len(plan.cameras) == 4
        assert len(plan.rules) == 4
        assert len(plan.videos) == 4
        assert plan.fingerprint
        assert plan.next_steps
        door = next(c for c in plan.cameras if c.slot_id == "door")
        assert door.name == "快餐店 · 门口"
        assert door.rule_ids
        assert session.query(Camera).count() == 0
        assert session.query(Rule).count() == 0
        assert session.query(PackDeployment).count() == 0
    finally:
        session.close()


def test_plan_legacy_requires_and_binds_camera(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        with pytest.raises(deploy.TargetError):
            deploy.plan("restaurant", session)  # 旧包必须明确选择，不默认第一台
        camera = _add_camera(session, tmp_path)
        plan = deploy.plan("restaurant", session, camera_id=camera.id)
        assert plan.mode == "existing_camera"
        assert plan.cameras[0].camera_id == camera.id
        assert len(plan.rules) == 3
        with pytest.raises(deploy.TargetError):
            deploy.plan("restaurant", session, camera_id=9999)
    finally:
        session.close()


def test_plan_unknown_pack_404(tmp_settings):
    init_db(tmp_settings.db_url)
    with _client() as client:
        resp = client.post("/api/packs/no-such/apply-plan", json={})
        assert resp.status_code == 404
        resp = client.post("/api/packs/restaurant/apply-plan", json={})
        assert resp.status_code == 422


# ---------- 原子应用与指纹 ----------


def test_apply_creates_deployment_and_disabled_rules(tmp_settings):
    """应用成功：部署 + 资源映射入库；摄像头 stopped、规则 disabled。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        outcome = deploy.apply("fast-food", session)
        assert outcome.deployment.status == "configuring"
        assert outcome.deployment.pack_id == "fast-food"
        assert outcome.deployment.pack_digest
        assert all(not r.enabled for r in outcome.rules)
        assert all(c.status == "stopped" for c in outcome.cameras)

        kinds = {(r.kind, r.ownership) for r in outcome.resources}
        assert ("camera", "created") in kinds
        assert ("rule", "created") in kinds
        assert ("video", "created") in kinds
        assert session.query(PackDeployment).count() == 1
        assert (session.query(PackDeploymentResource)
                .filter_by(deployment_id=outcome.deployment.id).count()
                == len(outcome.resources))
        slots = {r.camera_slot_id for r in outcome.resources}
        assert {"door", "counter", "kitchen", "hall"} <= slots
    finally:
        session.close()


def test_apply_fingerprint_mismatch_409(tmp_settings):
    init_db(tmp_settings.db_url)
    with _client() as client:
        plan = client.post("/api/packs/fast-food/apply-plan", json={}).json()
        resp = client.post("/api/packs/fast-food/apply",
                           json={"expected_fingerprint": plan["fingerprint"]})
        assert resp.status_code == 201, resp.text
        assert resp.json()["deployment"]["pack_id"] == "fast-food"

        resp = client.post("/api/packs/fast-food/apply",
                           json={"expected_fingerprint": "stale-fingerprint"})
        assert resp.status_code == 409
        assert "重新查看" in resp.json()["detail"]

        # 旧调用省略指纹仍可应用（兼容）
        resp = client.post("/api/packs/fast-food/apply", json={})
        assert resp.status_code == 201


def test_apply_rolls_back_files_and_rows_on_failure(tmp_settings, monkeypatch):
    """中途失败：DB 行全部回滚，已复制文件全部回收。"""
    init_db(tmp_settings.db_url)
    uploads = tmp_settings.data_dir / "uploads"
    calls = {"n": 0}
    real_probe = deploy.probe_resolution

    def flaky_probe(uri: str):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PackError("模拟第二路探测失败")
        return real_probe(uri)

    monkeypatch.setattr(deploy, "probe_resolution", flaky_probe)
    session = get_session()
    try:
        with pytest.raises(PackError, match="模拟第二路探测失败"):
            deploy.apply("fast-food", session)
        assert session.query(Camera).count() == 0
        assert session.query(Rule).count() == 0
        assert session.query(Video).count() == 0
        assert session.query(PackDeployment).count() == 0
        assert session.query(PackDeploymentResource).count() == 0
        leftovers = list(uploads.glob("*.mp4")) if uploads.is_dir() else []
        assert leftovers == []
    finally:
        session.close()


def test_apply_rejected_when_disk_full(tmp_settings, monkeypatch):
    init_db(tmp_settings.db_url)
    monkeypatch.setattr(deploy.shutil, "disk_usage",
                        lambda _p: type("U", (), {"free": 1})())
    with _client() as client:
        resp = client.post("/api/packs/fast-food/apply", json={})
        assert resp.status_code == 507
        session = get_session()
        try:
            assert session.query(Camera).count() == 0
        finally:
            session.close()


# ---------- 部署状态与校准 ----------


def _apply_fast_food(session):
    return deploy.apply("fast-food", session)


def test_deployment_degraded_when_resource_missing(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        outcome = _apply_fast_food(session)
        dep_id = outcome.deployment.id
        rule_res = next(r for r in outcome.resources if r.kind == "rule")
        session.delete(session.get(Rule, rule_res.resource_id))
        session.commit()

        out = deploy.get_deployment(session, dep_id)
        assert out.status == "degraded"
        missing = next(r for r in out.resources if r.id == rule_res.id)
        assert missing.exists is False
        # 状态已修正落库
        assert session.get(PackDeployment, dep_id).status == "degraded"

        with pytest.raises(deploy.ResourceStateError):
            deploy.set_resource_configured(session, dep_id, rule_res.id, True)
    finally:
        session.close()


def test_configure_enables_rule_and_activates_deployment(tmp_settings):
    """逐路校准：PATCH 启用规则；全部校准且有摄像头运行后部署 active。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        outcome = _apply_fast_food(session)
        dep_id = outcome.deployment.id
        gated = [r for r in outcome.resources if r.kind in ("camera", "rule")]
        for res in gated:
            out = deploy.set_resource_configured(session, dep_id, res.id, True)
            assert next(r for r in out.resources if r.id == res.id).configured
        # 规则已启用，但没有摄像头在跑 → 仍 configuring
        assert session.query(Rule).filter_by(enabled=True).count() == 4
        assert session.get(PackDeployment, dep_id).status == "configuring"

        camera = session.get(Camera, outcome.cameras[0].id)
        camera.status = "running"
        session.commit()
        out = deploy.get_deployment(session, dep_id)
        assert out.status == "active"
    finally:
        session.close()


def test_deployment_api_endpoints(tmp_settings):
    init_db(tmp_settings.db_url)
    with _client() as client:
        resp = client.post("/api/packs/fast-food/apply", json={})
        assert resp.status_code == 201
        dep = resp.json()["deployment"]

        resp = client.get(f"/api/pack-deployments/{dep['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "configuring"
        assert len(body["resources"]) == 12  # 4 摄像头 + 4 规则 + 4 视频

        rule_res = next(r for r in body["resources"] if r["kind"] == "rule")
        resp = client.patch(
            f"/api/pack-deployments/{dep['id']}/resources/{rule_res['id']}",
            json={"configured": True})
        assert resp.status_code == 200
        updated = next(r for r in resp.json()["resources"]
                       if r["id"] == rule_res["id"])
        assert updated["configured"] is True

        assert client.get("/api/pack-deployments/9999").status_code == 404
        assert client.patch(
            f"/api/pack-deployments/{dep['id']}/resources/9999",
            json={"configured": True}).status_code == 404


def test_legacy_apply_records_bound_camera(tmp_settings, tmp_path):
    """旧包：摄像头记为 bound 归属，规则禁用待校准。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        camera = _add_camera(session, tmp_path)
        outcome = deploy.apply("restaurant", session, camera_id=camera.id)
        cam_res = next(r for r in outcome.resources if r.kind == "camera")
        assert cam_res.ownership == "bound"
        assert cam_res.resource_id == camera.id
        assert all(not r.enabled for r in outcome.rules)
        assert {r.camera_slot_id for r in outcome.resources} == {"default"}
    finally:
        session.close()
