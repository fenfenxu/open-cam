"""规则 name 字段：迁移兜底、API 创建传/不传 name、presets 端点、apply 写入 name。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session, init_db
from opencam.models import Camera


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _make_camera(client) -> int:
    resp = client.post("/api/cameras", json={
        "name": "测试", "source_type": "file", "source_uri": "/tmp/x.mp4"})
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------- presets 端点 ----------

def test_presets_structure(client):
    resp = client.get("/api/rules/presets")
    assert resp.status_code == 200
    body = resp.json()
    presets = body["presets"]
    assert {p["type"] for p in presets} == {
        "zone_intrusion", "loitering", "object_count",
        "zone_count", "line_crossing"}
    for p in presets:
        assert p["display_name"]
        assert p["tagline"]
        assert p["description"]
        assert p["scenarios"], p["type"]
        assert isinstance(p["needs_zone"], bool)
        field_keys = {f["key"] for f in p["fields"]}
        assert "name" in field_keys and "cooldown" in field_keys
        # 所有规则都有生效时段可选字段
        assert "active_hours" in field_keys, p["type"]
    # loitering 有驻留秒数，object_count 有阈值
    loitering = next(p for p in presets if p["type"] == "loitering")
    assert any(f["key"] == "duration" and f["unit"] == "秒"
               for f in loitering["fields"])
    count = next(p for p in presets if p["type"] == "object_count")
    assert any(f["key"] == "threshold" for f in count["fields"])
    assert count["needs_zone"] is False
    # 新类型：zone_count 画多边形，line_crossing 画线 + 方向字段
    zone_count = next(p for p in presets if p["type"] == "zone_count")
    assert zone_count["zone_shape"] == "polygon"
    line = next(p for p in presets if p["type"] == "line_crossing")
    assert line["zone_shape"] == "line"
    assert any(f["key"] == "direction" for f in line["fields"])
    # 常用类别列表
    class_ids = {c["id"] for c in body["common_classes"]}
    assert "person" in class_ids
    assert body["classes_note"]


# ---------- API 创建规则：传 / 不传 name ----------

def test_create_rule_with_name(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/api/cameras/{camera_id}/rules", json={
        "name": "后厨禁入",
        "type": "zone_intrusion",
        "params": {"polygon": [[0, 0], [10, 0], [10, 10]]},
    })
    assert resp.status_code == 201
    assert resp.json()["name"] == "后厨禁入"


def test_create_rule_without_name_uses_default(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/api/cameras/{camera_id}/rules", json={
        "type": "loitering",
        "params": {"duration": 60},
    })
    assert resp.status_code == 201
    assert resp.json()["name"] == "徘徊逗留"


# ---------- 旧库自动迁移 ----------

def test_migration_adds_name_and_backfills(tmp_settings, tmp_path):
    """先建一个无 name 列的旧库，再 init_db，应补列并用类型中文名回填。"""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE rules (id INTEGER PRIMARY KEY, camera_id INTEGER, "
        "type VARCHAR(32), params JSON, enabled BOOLEAN, cooldown FLOAT)")
    conn.execute(
        "INSERT INTO rules (camera_id, type, params, enabled, cooldown) "
        "VALUES (1, 'zone_intrusion', '{}', 1, 30)")
    conn.commit()
    conn.close()

    init_db(f"sqlite:///{db_path}")
    session = get_session()
    try:
        from opencam.models import Rule

        rule = session.query(Rule).one()
        assert rule.name == "区域入侵"
        assert rule.type == "zone_intrusion"  # 旧数据未丢
    finally:
        session.close()


# ---------- apply 写入模板 name ----------

def test_apply_pack_writes_name(tmp_settings, tmp_path):
    import cv2
    import numpy as np

    from opencam.packs.apply import apply_pack

    video = tmp_path / "cam.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (320, 240))
    for _ in range(3):
        writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
    writer.release()

    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        camera = Camera(name="t", source_type="file", source_uri=str(video))
        session.add(camera)
        session.commit()

        created = apply_pack("restaurant", session, camera_id=camera.id).rules
        names = {r.name for r in created}
        assert "后厨区域入侵" in names
        assert "用餐区客流统计" in names
        assert "明火区域人员检测" in names
        assert all(r.name for r in created)
    finally:
        session.close()
