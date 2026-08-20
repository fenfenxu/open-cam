"""PackCatalog / manifest v2 / 详情与资产安全测试。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from opencam.db import init_db
from opencam.packs.catalog import (
    catalog,
    compute_fingerprint,
    safe_resolve_pack_path,
)
from opencam.packs.manifest import PackError, load_manifest, load_rule_templates
from opencam.packs.sanitize import sanitize_pack_readme


def _make_pack(base: Path, pack_id: str = "test-pack", **manifest_over) -> Path:
    pack = base / pack_id
    (pack / "rules").mkdir(parents=True)
    manifest = {
        "id": pack_id, "name": "测试包", "version": "1.0.0",
        "vertical": "测试", "min_opencam_version": "0.1.0",
        **manifest_over,
    }
    (pack / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 半屏入侵\ntype: zone_intrusion\ncooldown: 5\n"
        "params:\n  polygon: [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]\n"
        "  classes: [person]\n",
        encoding="utf-8")
    return pack


def _write_bytes(path: Path, data: bytes = b"demo") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------- manifest v2 ----------

def test_manifest_v2_loads_presentation_and_experience(tmp_path):
    pack = _make_pack(
        tmp_path,
        format_version=2,
        presentation={
            "tagline": "看清风险",
            "outcomes": [{"title": "发现入侵", "description": "区域有人即告警"}],
            "requirements": ["画面稳定"],
            "limitations": ["不识别身份"],
        },
        experience={
            "scenes": [{
                "id": "door-flow",
                "camera": "door",
                "title": "门口",
                "input_preview": "experience/in.mp4",
                "result_preview": "experience/out.mp4",
                "poster": "experience/poster.jpg",
                "events": "experience/events.json",
                "trial_source": "cameras/door.mp4",
            }],
        },
        cameras=[{"id": "door", "name": "门口", "source": "cameras/door.mp4",
                  "purpose": "客流", "placement": "正对门口"}],
    )
    _write_bytes(pack / "cameras" / "door.mp4")
    _write_bytes(pack / "experience" / "in.mp4")
    _write_bytes(pack / "experience" / "out.mp4")
    _write_bytes(pack / "experience" / "poster.jpg")
    (pack / "experience" / "events.json").write_text(json.dumps({
        "events": [{"at_sec": 1.5, "title": "进店", "result": "+1",
                    "intent": "observe"}],
    }), encoding="utf-8")
    # 规则需要挂 camera
    (pack / "rules" / "r1.yaml").write_text(
        "name: 半屏入侵\ntype: zone_intrusion\ncamera: door\ncooldown: 5\n"
        "params:\n  polygon: [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]\n"
        "  classes: [person]\n",
        encoding="utf-8")

    m = load_manifest(pack)
    assert m.format_version == 2
    assert m.presentation.tagline == "看清风险"
    assert m.experience.scenes[0].id == "door-flow"
    assert m.cameras[0].purpose == "客流"
    rules = load_rule_templates(pack)
    assert rules[0].id == "r1"


def test_legacy_pack_normalized_to_existing_camera(tmp_settings, tmp_path, monkeypatch):
    """旧包 cameras=null → 虚拟机位 + application.mode=existing_camera。"""
    import opencam.packs.catalog as cat

    pack = _make_pack(tmp_path, "legacy-norm")
    monkeypatch.setattr(cat, "builtin_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(cat, "installed_packs_dir",
                        lambda: tmp_settings.data_dir / "packs")

    detail = catalog.describe("legacy-norm")
    assert detail.availability == "available"
    assert detail.application.mode == "existing_camera"
    assert detail.cameras[0].id == "default"
    assert detail.rules[0].camera_id == "default"
    assert detail.experience.scenes[0].available is False
    # 详情不内嵌媒体字节 / 绝对路径
    dumped = detail.model_dump_json()
    assert str(pack) not in dumped
    assert "\\x00" not in dumped


def test_catalog_list_includes_incompatible(tmp_settings, tmp_path, monkeypatch):
    import opencam.packs.catalog as cat

    _make_pack(tmp_path, "too-new", min_opencam_version="99.0.0")
    monkeypatch.setattr(cat, "builtin_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(cat, "installed_packs_dir",
                        lambda: tmp_settings.data_dir / "packs")

    cards = {c.id: c for c in catalog.list()}
    assert "too-new" in cards
    assert cards["too-new"].availability == "incompatible"
    assert cards["too-new"].unavailable_reason


def test_catalog_list_includes_invalid_core(tmp_settings, tmp_path, monkeypatch):
    import opencam.packs.catalog as cat

    bad = tmp_path / "broken-core"
    (bad / "rules").mkdir(parents=True)
    (bad / "pack.yaml").write_text(
        "id: broken-core\nname: 坏包\nversion: 1.0.0\nvertical: x\n"
        "min_opencam_version: 0.1.0\n",
        encoding="utf-8")
    # 无规则文件 → 核心失败
    monkeypatch.setattr(cat, "builtin_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(cat, "installed_packs_dir",
                        lambda: tmp_settings.data_dir / "packs")

    cards = {c.id: c for c in catalog.list()}
    assert cards["broken-core"].availability == "unavailable"


def test_scene_degrades_when_preview_missing(tmp_settings, tmp_path, monkeypatch):
    import opencam.packs.catalog as cat

    pack = _make_pack(
        tmp_path, "deg-scene",
        format_version=2,
        cameras=[{"id": "door", "name": "门口", "source": "cameras/door.mp4"}],
        experience={"scenes": [{
            "id": "s1", "camera": "door", "title": "门口",
            "input_preview": "experience/missing.mp4",
            "result_preview": "experience/also-missing.mp4",
            "trial_source": "cameras/door.mp4",
        }]},
    )
    _write_bytes(pack / "cameras" / "door.mp4")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 半屏入侵\ntype: zone_intrusion\ncamera: door\ncooldown: 5\n"
        "params:\n  polygon: [[0,0],[1,0],[1,1],[0,1]]\n  classes: [person]\n",
        encoding="utf-8")
    monkeypatch.setattr(cat, "builtin_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(cat, "installed_packs_dir",
                        lambda: tmp_settings.data_dir / "packs")

    detail = catalog.describe("deg-scene")
    assert detail.availability == "available"  # 核心仍可用
    scene = detail.experience.scenes[0]
    assert scene.available is False
    assert scene.degrade_reason


def test_fingerprint_stable_and_skips_opencv(tmp_settings):
    from opencam.packs.installer import builtin_packs_dir
    root = builtin_packs_dir() / "restaurant"
    a = compute_fingerprint(root)
    b = compute_fingerprint(root)
    assert a == b
    assert len(a) == 32


# ---------- 路径 / Markdown 安全 ----------

def test_safe_resolve_rejects_traversal(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ok.txt").write_text("x", encoding="utf-8")
    assert safe_resolve_pack_path(root, "ok.txt") is not None
    assert safe_resolve_pack_path(root, "../ok.txt") is None
    assert safe_resolve_pack_path(root, "/etc/passwd") is None
    assert safe_resolve_pack_path(root, "..%2Fok.txt") is None
    assert safe_resolve_pack_path(root, "a/../../etc/passwd") is None


def test_safe_resolve_rejects_symlink_escape(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    outside = tmp_path / "secret.bin"
    outside.write_bytes(b"secret")
    link = root / "leak.bin"
    link.symlink_to(outside)
    assert safe_resolve_pack_path(root, "leak.bin") is None


def test_sanitize_strips_script_and_dangerous_urls():
    raw = (
        '# 标题\n\n正常 **加粗**\n\n'
        '<script>alert(1)</script>\n'
        '<a href="javascript:alert(1)">坏链</a>\n'
        '[好链](https://example.com)\n'
        '<img src=x onerror=alert(1)>\n'
    )
    out = sanitize_pack_readme(raw)
    assert "<script" not in out.lower()
    assert "javascript:" not in out.lower()
    assert "onerror" not in out.lower()
    assert "https://example.com" in out
    assert "加粗" in out or "strong" in out


# ---------- HTTP：详情 / 资产 ----------

def test_get_detail_and_asset_range_etag(tmp_settings, tmp_path, monkeypatch):
    from opencam.main import app
    import opencam.packs.catalog as cat

    pack = _make_pack(
        tmp_path, "asset-pack",
        format_version=2,
        cameras=[{"id": "door", "name": "门口", "source": "cameras/door.mp4"}],
        presentation={"tagline": "t", "cover": "experience/cover.jpg"},
        experience={"scenes": [{
            "id": "s1", "camera": "door", "title": "门口",
            "input_preview": "experience/in.mp4",
            "result_preview": "experience/out.mp4",
            "poster": "experience/cover.jpg",
            "trial_source": "cameras/door.mp4",
        }]},
    )
    _write_bytes(pack / "cameras" / "door.mp4", b"0" * 64)
    payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    _write_bytes(pack / "experience" / "in.mp4", payload)
    _write_bytes(pack / "experience" / "out.mp4", payload)
    _write_bytes(pack / "experience" / "cover.jpg", b"jpeg-bytes")
    (pack / "rules" / "r1.yaml").write_text(
        "name: 半屏入侵\ntype: zone_intrusion\ncamera: door\ncooldown: 5\n"
        "params:\n  polygon: [[0,0],[1,0],[1,1],[0,1]]\n  classes: [person]\n",
        encoding="utf-8")
    (pack / "README.md").write_text(
        "说明 <script>x</script>\n\n[文档](https://example.com)", encoding="utf-8")

    monkeypatch.setattr(cat, "builtin_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(cat, "installed_packs_dir",
                        lambda: tmp_settings.data_dir / "packs")

    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.get("/api/packs/asset-pack")
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability"] == "available"
        assert body["presentation"]["tagline"] == "t"
        assert "<script" not in body["readme_html"].lower()
        assert "https://example.com" in body["readme_html"]
        # 不内嵌媒体字节
        assert "ABCDEFG" not in resp.text

        cover_id = body["presentation"]["cover_asset_id"]
        assert cover_id and cover_id.startswith("a_")

        # 404：未登记资产 id（路径穿越由 safe_resolve 单测覆盖；
        # TestClient 会解码 %2F 并规范化 ../，不宜在 HTTP 路径里测穿越）
        assert client.get(
            "/api/packs/asset-pack/assets/not-registered"
        ).status_code == 404
        assert client.get(
            "/api/packs/asset-pack/assets/a_0000000000000000"
        ).status_code == 404

        asset = client.get(f"/api/packs/asset-pack/assets/{cover_id}")
        assert asset.status_code == 200
        assert asset.headers.get("etag")
        assert asset.headers.get("accept-ranges") == "bytes"
        assert asset.content == b"jpeg-bytes"

        etag = asset.headers["etag"]
        cached = client.get(
            f"/api/packs/asset-pack/assets/{cover_id}",
            headers={"If-None-Match": etag},
        )
        assert cached.status_code == 304

        input_id = body["experience"]["scenes"][0]["input_asset_id"]
        ranged = client.get(
            f"/api/packs/asset-pack/assets/{input_id}",
            headers={"Range": "bytes=0-4"},
        )
        assert ranged.status_code == 206
        assert ranged.content == b"ABCDE"
        assert ranged.headers.get("content-range", "").startswith("bytes 0-4/")


def test_get_detail_404(tmp_settings):
    from opencam.main import app
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        assert client.get("/api/packs/no-such-pack-xyz").status_code == 404


def test_list_packs_cards_view(tmp_settings, tmp_path, monkeypatch):
    """view=cards 返回规范化 PackCard（含不可用包）；默认 brief 保持兼容。"""
    from opencam.main import app
    import opencam.packs.catalog as cat
    import opencam.packs.installer as inst

    _make_pack(tmp_path, "cards-pack")
    (tmp_path / "broken-pack").mkdir()
    (tmp_path / "broken-pack" / "pack.yaml").write_text(
        "id: broken-pack\nversion: [oops\n", encoding="utf-8")
    installed = tmp_settings.data_dir / "packs"
    for mod in (cat, inst):
        monkeypatch.setattr(mod, "builtin_packs_dir", lambda: tmp_path)
        monkeypatch.setattr(mod, "installed_packs_dir", lambda: installed)

    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        cards = client.get("/api/packs", params={"view": "cards"})
        assert cards.status_code == 200
        by_id = {c["id"]: c for c in cards.json()}
        assert by_id["cards-pack"]["availability"] == "available"
        assert by_id["cards-pack"]["application_mode"] == "existing_camera"
        assert "fingerprint" in by_id["cards-pack"]
        # 无效包不静默消失，带原因
        assert by_id["broken-pack"]["availability"] == "unavailable"
        assert by_id["broken-pack"]["unavailable_reason"]

        brief = client.get("/api/packs")
        assert brief.status_code == 200
        brief_ids = {p["id"] for p in brief.json()}
        assert "cards-pack" in brief_ids
        assert "broken-pack" not in brief_ids  # brief 跳过无效包（兼容行为）


def test_apply_unchanged_after_catalog(tmp_settings, tmp_path):
    """Catalog 不改变现有 apply 行为。"""
    from opencam.db import get_session
    from opencam.models import Camera
    from opencam.packs.apply import apply_pack
    import cv2
    import numpy as np

    init_db(tmp_settings.db_url)
    video = tmp_path / "cam.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (320, 240))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for _ in range(5):
        writer.write(frame)
    writer.release()

    session = get_session()
    try:
        camera = Camera(name="t", source_type="file", source_uri=str(video))
        session.add(camera)
        session.commit()
        created = apply_pack("restaurant", session, camera_id=camera.id).rules
        assert len(created) == 3
    finally:
        session.close()


def test_builtin_detail_shapes(tmp_settings):
    """内置包均可 describe；fast-food 为 create_cameras，旧包为 existing_camera。"""
    ff = catalog.describe("fast-food")
    assert ff.availability == "available"
    assert ff.application.mode == "create_cameras"
    assert ff.application.camera_count == 4
    assert {c.id for c in ff.cameras} == {"door", "counter", "kitchen", "hall"}
    assert all(r.id for r in ff.rules)

    rest = catalog.describe("restaurant")
    assert rest.application.mode == "existing_camera"
    assert rest.cameras[0].id == "default"
