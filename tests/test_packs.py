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

        created = apply_pack("restaurant", camera_id, session)
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
            apply_pack("no-such-pack", camera.id, session)
    finally:
        session.close()


def test_apply_fast_food_pack(tmp_settings, tmp_path):
    """fast-food 包：line 换算为像素、active_hours 保留、name 写入。"""
    init_db(tmp_settings.db_url)
    video = tmp_path / "cam.mp4"
    _make_video(video)
    session = get_session()
    try:
        camera = Camera(name="t", source_type="file", source_uri=str(video))
        session.add(camera)
        session.commit()

        created = apply_pack("fast-food", camera.id, session)
        assert len(created) == 4
        by_type = {}
        for r in created:
            by_type.setdefault(r.type, r)
        # 越线规则：相对坐标换算为 320x240 像素
        line_rule = by_type["line_crossing"]
        assert line_rule.name == "门口进出客流"
        assert line_rule.params["line"] == [[96.0, 120.0], [224.0, 120.0]]
        assert line_rule.params["direction"] == "both"
        # 闭店入侵带生效时段
        after_hours = next(r for r in created if r.name == "闭店后入侵")
        assert after_hours.params["active_hours"] == "22:00-07:00"
        # 区域人数
        assert by_type["zone_count"].params["threshold"] == 5
    finally:
        session.close()
