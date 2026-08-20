"""PackDeployment：plan/apply 指纹、原子回滚、部署状态与校准启用。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session, init_db
from opencam.models import (
    CAMERA_RUNNING,
    Camera,
    DEPLOY_ACTIVE,
    DEPLOY_CONFIGURING,
    DEPLOY_DEGRADED,
    PackDeployment,
    PackDeploymentResource,
    Rule,
)
from opencam.packs.catalog import compute_fingerprint
from opencam.packs.deployment import DeploymentError, apply_pack, pack_deployment
from opencam.packs.installer import get_pack


def _make_video(path: Path, w: int = 320, h: int = 240) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
    assert writer.isOpened()
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    for _ in range(5):
        writer.write(frame)
    writer.release()


def test_apply_plan_fast_food(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        plan = pack_deployment.plan("fast-food", session=session)
        assert plan.mode == "create_cameras"
        assert plan.fingerprint
        assert len(plan.cameras) == 4
        assert len(plan.rules) == 4
        assert len(plan.videos) == 4
        assert "不自动启动摄像头" in plan.will_not
    finally:
        session.close()


def test_apply_plan_legacy_requires_camera(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    video = tmp_path / "cam.mp4"
    _make_video(video)
    session = get_session()
    try:
        with pytest.raises(Exception, match="请指定"):
            pack_deployment.plan("restaurant", session=session)
        cam = Camera(name="t", source_type="file", source_uri=str(video))
        session.add(cam)
        session.commit()
        plan = pack_deployment.plan("restaurant", camera_id=cam.id, session=session)
        assert plan.mode == "existing_camera"
        assert len(plan.cameras) == 1
        assert plan.cameras[0].action == "bind"
        assert len(plan.rules) == 3
    finally:
        session.close()


def test_apply_fingerprint_mismatch_409(tmp_settings):
    from opencam.main import app

    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post("/api/packs/fast-food/apply",
                           json={"expected_fingerprint": "deadbeef" * 4})
        assert resp.status_code == 409
        assert "内容已变化" in resp.json()["detail"]


def test_apply_with_matching_fingerprint(tmp_settings):
    from opencam.main import app

    pack = get_pack("fast-food")
    assert pack is not None
    fp = compute_fingerprint(pack.base_dir)
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        plan = client.post("/api/packs/fast-food/apply-plan", json={}).json()
        assert plan["fingerprint"] == fp
        resp = client.post("/api/packs/fast-food/apply",
                           json={"expected_fingerprint": fp})
        assert resp.status_code == 201
        body = resp.json()
        assert body["deployment_id"] is not None
        assert len(body["cameras"]) == 4
        assert all(not r["enabled"] for r in body["rules"])

        dep = client.get(f"/api/pack-deployments/{body['deployment_id']}").json()
        assert dep["status"] == DEPLOY_CONFIGURING
        assert dep["pack_digest"] == fp
        kinds = {r["kind"] for r in dep["resources"]}
        assert kinds == {"camera", "rule", "video"}


def test_apply_creates_disabled_rules_and_deployment(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        result = apply_pack("fast-food", session)
        assert result.deployment is not None
        assert all(not r.enabled for r in result.rules)
        assert session.query(PackDeployment).count() == 1
        assert session.query(PackDeploymentResource).count() == 12  # 4+4+4
    finally:
        session.close()


def test_apply_file_cleanup_on_db_failure(tmp_settings, monkeypatch):
    """DB flush 失败时已复制的演示片应被回收。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    uploads = tmp_settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    before = set(uploads.glob("*")) if uploads.exists() else set()

    real_flush = session.flush
    calls = {"n": 0}

    def boom_flush(*args, **kwargs):
        calls["n"] += 1
        # 让第一个 Video/Camera 写入后的 flush 炸掉
        if calls["n"] >= 3:
            raise RuntimeError("simulated db failure")
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", boom_flush)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            apply_pack("fast-food", session)
        after = set(uploads.glob("*")) if uploads.exists() else set()
        assert after == before
        assert session.query(Camera).count() == 0
        assert session.query(PackDeployment).count() == 0
    finally:
        session.close()


def test_mark_configured_enables_rule_and_active(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        result = apply_pack("fast-food", session)
        dep_id = result.deployment.id
        # 标记全部规则完成
        rows = (session.query(PackDeploymentResource)
                .filter_by(deployment_id=dep_id, kind="rule").all())
        for row in rows:
            out = pack_deployment.mark_configured(dep_id, row.id, session)
            assert session.get(Rule, row.resource_id).enabled is True

        # 尚无摄像头运行 → 仍 configuring
        assert out.status == DEPLOY_CONFIGURING

        # 启动一路摄像头后应 active
        cam = result.cameras[0]
        cam.status = CAMERA_RUNNING
        session.commit()
        out = pack_deployment.get(dep_id, session)
        assert out.status == DEPLOY_ACTIVE
    finally:
        session.close()


def test_missing_resource_degrades(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        result = apply_pack("fast-food", session)
        dep_id = result.deployment.id
        # 删除一路摄像头
        session.delete(result.cameras[0])
        session.commit()
        out = pack_deployment.get(dep_id, session)
        assert out.status == DEPLOY_DEGRADED
        assert any(r.missing for r in out.resources if r.kind == "camera")
    finally:
        session.close()


def test_legacy_http_apply_compatible(tmp_settings, tmp_path):
    from opencam.main import app

    init_db(tmp_settings.db_url)
    video = tmp_path / "cam.mp4"
    _make_video(video)
    with TestClient(app) as client:
        cam = client.post("/api/cameras", json={
            "name": "t", "source_type": "file", "source_uri": str(video),
        }).json()
        # 旧调用不传 fingerprint，仍成功；响应多出可选 deployment_id
        resp = client.post("/api/packs/restaurant/apply",
                           json={"camera_id": cam["id"]})
        assert resp.status_code == 201
        body = resp.json()
        assert "cameras" in body and "rules" in body
        assert body["deployment_id"] is not None
        assert len(body["rules"]) == 3
        assert all(not r["enabled"] for r in body["rules"])
