"""内置方案包产品内容与快餐店体验资产的合同测试（无网络、不触碰真实模型）。

校验逻辑实现于 scripts/check_pack_experience.py，这里按方面拆分断言：
- 四包 presentation 产品文案完整且不超出实际检测能力；
- fast-food 四场景资产齐全、媒体满足浏览器播放合同；
- 事件时间线与规则默认意图一致；
- 试跑源经 MockDetector + 包内规则重放，命中与声明事件一致。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_pack_experience", REPO_ROOT / "scripts" / "check_pack_experience.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

PACKS_DIR = REPO_ROOT / "packs"
FAST_FOOD = check.load_pack_yaml("fast-food")
SCENES = {s["id"]: s for s in FAST_FOOD["experience"]["scenes"]}


def test_builtin_packs_presentation_contract():
    """四个内置包都必须有完整的业务文案（结果/要求/限制）。"""
    for pack_id in check.BUILTIN_PACKS:
        assert check.check_presentation(pack_id, check.load_pack_yaml(pack_id)) == []


def test_legacy_packs_degrade_without_experience():
    """餐饮/零售/美发三包：无 cameras/experience，走单摄像头 + 无媒体的降级路径。"""
    for pack_id in ("restaurant", "retail-chain", "salon"):
        assert check.check_legacy_degrade(pack_id, check.load_pack_yaml(pack_id)) == []


def test_copy_does_not_overclaim_capabilities():
    """文案不得承诺模型不具备的能力，且必须明示身份识别限制。"""
    for pack_id in check.BUILTIN_PACKS:
        data = check.load_pack_yaml(pack_id)
        text = str(data.get("presentation", {}))
        for forbidden in ("人脸识别", "工服识别", "跨摄像头识别", "Re-ID"):
            assert forbidden not in text, f"{pack_id} 文案越界承诺: {forbidden}"
        limitations = " ".join(data["presentation"]["limitations"])
        assert "不能识别身份" in limitations, f"{pack_id} 未明示身份识别限制"


def test_fast_food_declares_four_scenes():
    assert set(SCENES) == set(check.FAST_FOOD_SCENES)
    assert {s["camera"] for s in SCENES.values()} == {"door", "counter", "kitchen", "hall"}
    for scene in SCENES.values():
        for key in check.SCENE_ASSET_KEYS:
            assert scene.get(key), f"{scene['id']} 缺少 {key}"


@pytest.mark.parametrize("scene_id", check.FAST_FOOD_SCENES)
def test_scene_media_contract(scene_id):
    """原始/结果视频为 H.264、8-20 秒、尺寸受控；海报与视频同尺寸。"""
    scene = SCENES[scene_id]
    errors, input_info = check.check_video(
        PACKS_DIR / "fast-food" / scene["trial_source"], "trial")
    assert errors == []
    result_errors, _ = check.check_video(
        PACKS_DIR / "fast-food" / scene["result_preview"], "result")
    assert result_errors == []
    assert check.check_poster(PACKS_DIR / "fast-food" / scene["poster"],
                              input_info["width"], input_info["height"], "poster") == []


@pytest.mark.parametrize("scene_id", check.FAST_FOOD_SCENES)
def test_scene_events_contract(scene_id):
    """事件时间线：at_sec 落在时长内，intent 与规则默认意图一致。"""
    scene = SCENES[scene_id]
    pack_dir = PACKS_DIR / "fast-food"
    info = check.video_info(pack_dir / scene["trial_source"])
    events = check.load_events(pack_dir / scene["events"])
    stubs = check.make_rule_stubs(pack_dir, scene["camera"],
                                  info["width"], info["height"])
    assert len(stubs) == 1
    assert check.check_events(events, info["duration"], stubs[0].type, scene_id) == []


@pytest.mark.parametrize("scene_id", check.FAST_FOOD_SCENES)
def test_trial_source_replay_matches_declared_events(scene_id):
    """试跑源经当前 detector（mock）+ 包内规则重放，命中与声明事件一致。"""
    scene = SCENES[scene_id]
    pack_dir = PACKS_DIR / "fast-food"
    events = check.load_events(pack_dir / scene["events"])
    assert check.check_scene_replay(pack_dir, scene, events, scene_id) == []
