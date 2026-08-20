"""方案包测试：manifest 校验、目录/zip 安装、应用坐标换算、卸载。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from opencam.db import get_session, init_db
from opencam.models import Camera, Rule
from opencam.packs import installer
from opencam.packs.apply import apply_pack, scale_params
from opencam.packs.manifest import PackError, load_manifest, load_rule_templates

W, H = 320, 240


def _make_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (W, H))
    assert writer.isOpened()
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    for _ in range(5):
        writer.write(frame)
    writer.release()


def _make_pack(base: Path, pack_id: str = "test-pack", **manifest_over) -> Path:
    """造一个最小合法包目录。"""
    pack = base / pack_id
    (pack / "rules").mkdir(parents=True)
    manifest = {
        "id": pack_id, "name": "测试包", "version": "1.0.0",
        "vertical": "测试", "min_opencam_version": "0.1.0",
        **manifest_over,
    }
    import yaml

    (pack / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 半屏入侵\ntype: zone_intrusion\ncooldown: 5\n"
        "params:\n  polygon: [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]\n"
        "  classes: [person]\n",
        encoding="utf-8")
    return pack


# ---------- manifest 校验 ----------

def test_builtin_packs_valid(tmp_settings):
    """仓库内置的四个包必须全部通过校验。"""
    packs = installer.list_packs()
    ids = {p["id"] for p in packs}
    assert {"retail-chain", "salon", "restaurant", "fast-food"} <= ids
    for p in packs:
        assert p["rules"], p["id"]


def test_manifest_missing_field(tmp_path):
    bad = tmp_path / "bad"
    (bad / "rules").mkdir(parents=True)
    (bad / "pack.yaml").write_text("id: bad\n", encoding="utf-8")
    with pytest.raises(PackError, match="校验失败"):
        load_manifest(bad)


def test_manifest_version_too_new(tmp_path):
    pack = _make_pack(tmp_path, min_opencam_version="99.0.0")
    with pytest.raises(PackError, match="校验失败"):
        load_manifest(pack)


def test_rule_template_validation(tmp_path):
    pack = _make_pack(tmp_path)
    (pack / "rules" / "bad.yaml").write_text(
        "name: 错误类型\ntype: no_such_rule\n", encoding="utf-8")
    with pytest.raises(PackError, match="bad.yaml"):
        load_rule_templates(pack)


# ---------- 安装 / 卸载 ----------

def test_install_from_dir_and_uninstall(tmp_settings, tmp_path):
    pack = _make_pack(tmp_path)
    brief = installer.install(str(pack))
    assert brief["id"] == "test-pack"
    assert brief["origin"] == "installed"
    # 已落盘到 data/packs
    assert (tmp_settings.data_dir / "packs" / "test-pack" / "pack.yaml").exists()

    installer.uninstall("test-pack")
    assert not (tmp_settings.data_dir / "packs" / "test-pack").exists()


def test_install_from_zip(tmp_settings, tmp_path):
    pack = _make_pack(tmp_path)
    zip_path = tmp_path / "test-pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in pack.rglob("*"):
            zf.write(f, f.relative_to(pack))  # zip 内不带外层目录
    brief = installer.install(str(zip_path))
    assert brief["id"] == "test-pack"


def test_install_from_uploaded_zip(tmp_settings, tmp_path):
    from fastapi.testclient import TestClient
    from opencam.main import app

    pack = _make_pack(tmp_path)
    zip_path = tmp_path / "test-pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in pack.rglob("*"):
            zf.write(f, f.relative_to(pack))

    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post(
            "/api/packs/install-upload",
            files={"file": ("test-pack.zip", zip_path.read_bytes(), "application/zip")},
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == "test-pack"


def test_uninstall_builtin_rejected(tmp_settings):
    with pytest.raises(PackError, match="内置包不可卸载"):
        installer.uninstall("restaurant")


def test_install_bad_source(tmp_settings):
    with pytest.raises(PackError):
        installer.install("/nonexistent/whatever")


# ---------- 应用与坐标换算 ----------

def test_scale_params():
    params = {"polygon": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0]],
              "classes": ["person"]}
    out = scale_params(params, 320, 240)
    assert out["polygon"] == [[0.0, 0.0], [160.0, 0.0], [160.0, 240.0]]
    assert out["classes"] == ["person"]  # 非坐标参数原样保留


def test_scale_params_line():
    """line 相对坐标同样换算；active_hours 等参数原样保留。"""
    params = {"line": [[0.3, 0.5], [0.7, 0.5]], "direction": "both",
              "active_hours": "22:00-07:00"}
    out = scale_params(params, 320, 240)
    assert out["line"] == [[96.0, 120.0], [224.0, 120.0]]
    assert out["direction"] == "both"
    assert out["active_hours"] == "22:00-07:00"


def test_apply_builtin_pack(tmp_settings, tmp_path):
    """应用内置包到文件源摄像头：规则入库且坐标按 320x240 换算。"""
    init_db(tmp_settings.db_url)
    video = tmp_path / "cam.mp4"
    _make_video(video)
    session = get_session()
    try:
        camera = Camera(name="t", source_type="file", source_uri=str(video))
        session.add(camera)
        session.commit()
        camera_id = camera.id

        created = apply_pack("restaurant", session, camera_id=camera_id).rules
        assert len(created) == 3
        # 后厨入侵模板的 polygon x 最大为 0.4，明火区域最大为 0.25
        kitchen = next(r for r in created
                       if r.type == "zone_intrusion"
                       and max(p[0] for p in r.params["polygon"]) == 128.0)
        # 模板左边界 0.4 * 320 = 128.0，下边界 1.0 * 240 = 240.0
        poly = kitchen.params["polygon"]
        assert [128.0, 48.0] in poly  # [0.4, 0.2] 换算结果
        assert [128.0, 240.0] in poly
        # 规则确实入库
        assert session.query(Rule).filter_by(camera_id=camera_id).count() == 3
    finally:
        session.close()


def test_apply_unknown_pack(tmp_settings, tmp_path):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        camera = Camera(name="t", source_type="file", source_uri="x.mp4")
        session.add(camera)
        session.commit()
        with pytest.raises(PackError, match="不存在"):
            apply_pack("no-such-pack", session, camera_id=camera.id)
    finally:
        session.close()


def test_apply_fast_food_pack(tmp_settings):
    """fast-food 新包：不传 camera_id；line 按演示片 640x360 换算。"""
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        created = apply_pack("fast-food", session).rules
        assert len(created) == 4
        by_type = {}
        for r in created:
            by_type.setdefault(r.type, r)
        line_rule = by_type["line_crossing"]
        assert line_rule.name == "门口进出客流"
        assert line_rule.params["line"] == [[192.0, 180.0], [448.0, 180.0]]
        assert line_rule.params["direction"] == "both"
        after_hours = next(r for r in created if r.name == "闭店后入侵")
        assert after_hours.params["active_hours"] == "22:00-07:00"
        assert by_type["zone_count"].params["threshold"] == 5
    finally:
        session.close()


def test_list_brief_cameras_dual_track(tmp_settings):
    packs = {p["id"]: p for p in installer.list_packs()}
    assert packs["fast-food"]["cameras"] is not None
    names = {c["name"] for c in packs["fast-food"]["cameras"]}
    assert names == {"门口", "点餐", "后厨", "店内"}
    assert {r.get("camera") for r in packs["fast-food"]["rules"]} == {
        "door", "counter", "kitchen", "hall"}
    assert packs["restaurant"]["cameras"] is None
    assert all("camera" not in r for r in packs["restaurant"]["rules"])


def test_apply_fast_food_creates_four_cameras(tmp_settings):
    from opencam.models import Video

    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        result = apply_pack("fast-food", session)
        names = sorted(c.name for c in result.cameras)
        assert names == ["快餐店 · 后厨", "快餐店 · 店内", "快餐店 · 点餐", "快餐店 · 门口"]
        assert all(c.status == "stopped" and c.source_type == "file"
                   for c in result.cameras)
        uploads = tmp_settings.data_dir / "uploads"
        for cam in result.cameras:
            path = Path(cam.source_uri)
            assert path.exists()
            assert uploads.resolve() in path.resolve().parents
        by_name = {c.name: c.id for c in result.cameras}
        rules = {r.name: r for r in result.rules}
        assert rules["门口进出客流"].camera_id == by_name["快餐店 · 门口"]
        assert rules["点餐区排队超员"].camera_id == by_name["快餐店 · 点餐"]
        assert rules["后厨闯入"].camera_id == by_name["快餐店 · 后厨"]
        assert rules["闭店后入侵"].camera_id == by_name["快餐店 · 店内"]
        assert rules["闭店后入侵"].params["active_hours"] == "22:00-07:00"
        assert session.query(Video).count() == 4
    finally:
        session.close()


def test_apply_fast_food_second_set_gets_suffix(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        first = apply_pack("fast-food", session)
        first_uri = next(c.source_uri for c in first.cameras
                         if c.name == "快餐店 · 门口")
        second = apply_pack("fast-food", session)
        names = {c.name for c in second.cameras}
        assert "快餐店 · 门口 (2)" in names
        session.refresh(first.cameras[0])
        still = session.query(Camera).filter_by(name="快餐店 · 门口").one()
        assert still.source_uri == first_uri
    finally:
        session.close()


def test_preview_files_openable(tmp_settings):
    from opencam.packs.installer import builtin_packs_dir
    import cv2
    for name in ("door", "counter", "kitchen", "hall"):
        path = builtin_packs_dir() / "fast-food" / "cameras" / f"{name}.mp4"
        assert path.is_file(), path
        cap = cv2.VideoCapture(str(path))
        assert cap.isOpened()
        cap.release()


def test_apply_fast_food_rejects_camera_id(tmp_settings):
    from fastapi.testclient import TestClient
    from opencam.main import app
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post("/api/packs/fast-food/apply", json={"camera_id": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "该方案会创建摄像头，不要指定 camera_id"


def test_apply_legacy_requires_camera_id(tmp_settings, tmp_path):
    from fastapi.testclient import TestClient
    from opencam.main import app
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post("/api/packs/restaurant/apply", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "请指定要应用的摄像头"
        video = tmp_path / "cam.mp4"
        _make_video(video)
        cam = client.post("/api/cameras", json={
            "name": "t", "source_type": "file", "source_uri": str(video),
        }).json()
        resp = client.post("/api/packs/restaurant/apply",
                           json={"camera_id": cam["id"]})
        assert resp.status_code == 201
        body = resp.json()
        assert "cameras" in body and "rules" in body
        assert body["cameras"][0]["id"] == cam["id"]
        assert len(body["rules"]) == 3
