#!/usr/bin/env python3
"""校验内置方案包的产品内容与体验资产合同（无网络依赖）。

检查项：
1. 四个内置包 presentation（tagline/outcomes/requirements/limitations）完整；
2. fast-food 四个场景均声明 input/result/poster/events/trial_source 且文件存在；
3. 媒体合同：H.264 MP4（有 ffprobe 时追加校验 codec=h264 + yuv420p）、
   时长 8-20 秒、分辨率 ≤1280x720、单文件 ≤2MB、海报与视频同尺寸；
4. events JSON：at_sec 落在视频时长内、intent ∈ {observe, alert} 且与规则默认意图一致；
5. 重放合同：trial_source 经 MockDetector + 包内规则（RuleEngine）重放，
   命中与 events JSON 声明一致（条数相等、时间差 ≤0.75 秒）。

用法：uv run python scripts/check_pack_experience.py
任一不满足以非零码退出。测试见 tests/test_pack_experience_assets.py。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from opencam.detection.detector import MockDetector  # noqa: E402
from opencam.detection.rules import RuleEngine  # noqa: E402
from opencam.models import default_intent  # noqa: E402
from opencam.packs.apply import scale_params  # noqa: E402
from opencam.packs.manifest import load_rule_templates  # noqa: E402

BUILTIN_PACKS = ("fast-food", "restaurant", "retail-chain", "salon")
PACKS_DIR = REPO_ROOT / "packs"
FAST_FOOD_SCENES = ("door-flow", "queue-count", "kitchen-intrusion", "after-hours")

# 重放时钟固定在本地 23:00：闭店类规则（active_hours 跨午夜）也能触发
NIGHT_BASE = time.mktime((2026, 1, 5, 23, 0, 0, 0, 0, -1))

MAX_VIDEO_BYTES = 2 * 1024 * 1024
MAX_WIDTH, MAX_HEIGHT = 1280, 720
MIN_DURATION, MAX_DURATION = 8.0, 20.0
REPLAY_TIME_TOLERANCE = 0.75
_H264_FOURCC = {"avc1", "H264", "h264", "x264"}
INTENTS = ("observe", "alert")


def load_pack_yaml(pack_id: str) -> dict:
    path = PACKS_DIR / pack_id / "pack.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------- 产品文案合同 ----------

def check_presentation(pack_id: str, data: dict) -> list[str]:
    """四包统一的产品文案合同：tagline/outcomes/requirements/limitations。"""
    errors: list[str] = []
    pres = data.get("presentation")
    if not isinstance(pres, dict):
        return [f"{pack_id}: 缺少 presentation"]
    if not (pres.get("tagline") or "").strip():
        errors.append(f"{pack_id}: presentation.tagline 为空")
    outcomes = pres.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        errors.append(f"{pack_id}: presentation.outcomes 至少需要 2 条")
    else:
        for i, item in enumerate(outcomes):
            if not isinstance(item, dict) or not (item.get("title") or "").strip() \
                    or not (item.get("description") or "").strip():
                errors.append(f"{pack_id}: outcomes[{i}] 需要非空 title/description")
    for key in ("requirements", "limitations"):
        value = pres.get(key)
        if not isinstance(value, list) or not value \
                or any(not (str(v).strip()) for v in value):
            errors.append(f"{pack_id}: presentation.{key} 至少需要 1 条非空内容")
    return errors


def check_legacy_degrade(pack_id: str, data: dict) -> list[str]:
    """旧包：无 cameras/experience，走"应用到已有摄像头 + 无演示媒体"的降级路径。"""
    errors: list[str] = []
    if data.get("cameras"):
        errors.append(f"{pack_id}: 旧包不应声明 cameras（应走单摄像头降级）")
    if data.get("experience"):
        errors.append(f"{pack_id}: 无演示媒体的包不应声明 experience")
    return errors


# ---------- 媒体合同 ----------

def _fourcc_str(value: float) -> str:
    v = int(value)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))


def video_info(path: Path) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"无法打开视频: {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        return {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": fps,
            "frames": int(frames),
            "duration": frames / fps if fps > 0 else 0.0,
            "fourcc": _fourcc_str(cap.get(cv2.CAP_PROP_FOURCC)),
        }
    finally:
        cap.release()


def ffprobe_stream(path: Path) -> dict | None:
    """有 ffprobe 时返回 {codec_name, pix_fmt}，否则返回 None。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout or "{}").get("streams") or []
    return streams[0] if streams else None


def check_video(path: Path, label: str) -> tuple[list[str], dict | None]:
    errors: list[str] = []
    if not path.is_file():
        return [f"{label}: 文件不存在 {path.name}"], None
    if path.stat().st_size > MAX_VIDEO_BYTES:
        errors.append(f"{label}: {path.name} 超过 2MB 大小上限")
    try:
        info = video_info(path)
    except ValueError as exc:
        return [f"{label}: {exc}"], None
    if info["fourcc"] not in _H264_FOURCC:
        errors.append(
            f"{label}: {path.name} 编码 {info['fourcc']!r} 不是浏览器可用的 H.264")
    stream = ffprobe_stream(path)
    if stream is not None:
        if stream.get("codec_name") != "h264":
            errors.append(f"{label}: {path.name} codec={stream.get('codec_name')} 不是 h264")
        if stream.get("pix_fmt") != "yuv420p":
            errors.append(f"{label}: {path.name} pix_fmt={stream.get('pix_fmt')} 不是 yuv420p")
    if not (MIN_DURATION <= info["duration"] <= MAX_DURATION):
        errors.append(
            f"{label}: {path.name} 时长 {info['duration']:.1f}s 不在 8-20 秒范围")
    if info["width"] > MAX_WIDTH or info["height"] > MAX_HEIGHT:
        errors.append(
            f"{label}: {path.name} 分辨率 {info['width']}x{info['height']} 超过 1280x720")
    return errors, info


def check_poster(path: Path, width: int, height: int, label: str) -> list[str]:
    import cv2

    if not path.is_file():
        return [f"{label}: 海报不存在 {path.name}"]
    img = cv2.imread(str(path))
    if img is None:
        return [f"{label}: 海报无法解码 {path.name}"]
    h, w = img.shape[:2]
    if (w, h) != (width, height):
        return [f"{label}: 海报尺寸 {w}x{h} 与视频 {width}x{height} 不一致"]
    return []


# ---------- 事件时间线与重放合同 ----------

def load_events(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError(f"{path.name}: 缺少 events 列表")
    return events


def check_events(events: list[dict], duration: float, rule_type: str,
                 label: str) -> list[str]:
    errors: list[str] = []
    if not events:
        errors.append(f"{label}: events 为空")
    for i, event in enumerate(events):
        at = event.get("at_sec")
        if not isinstance(at, (int, float)) or not (0 <= at < duration):
            errors.append(f"{label}: events[{i}].at_sec={at} 不在视频时长 {duration:.1f}s 内")
        if not (event.get("title") or "").strip() or not (event.get("result") or "").strip():
            errors.append(f"{label}: events[{i}] 需要非空 title/result")
        intent = event.get("intent")
        if intent not in INTENTS:
            errors.append(f"{label}: events[{i}].intent={intent!r} 非法")
        elif intent != default_intent(rule_type):
            errors.append(
                f"{label}: events[{i}].intent={intent} 与规则 {rule_type} "
                f"默认意图 {default_intent(rule_type)} 不一致")
    return errors


def make_rule_stubs(pack_dir: Path, camera_id: str, width: int,
                    height: int) -> list[SimpleNamespace]:
    """把包内某机位的规则模板换算成像素坐标后的可评估桩对象。"""
    stubs = []
    for i, tpl in enumerate(t for t in load_rule_templates(pack_dir)
                            if t.camera == camera_id):
        stubs.append(SimpleNamespace(
            id=i + 1, type=tpl.type,
            params=scale_params(tpl.params, width, height),
            cooldown=tpl.cooldown, enabled=True))
    return stubs


def run_rule_engine(frames_detections: list[list], rules: list,
                    fps: float, clock_base: float = NIGHT_BASE) -> list[tuple[float, str, dict]]:
    """按帧重放规则引擎，返回 [(t_sec, rule_type, detail)]。"""
    now = [clock_base]
    engine = RuleEngine(clock=lambda: now[0])
    hits: list[tuple[float, str, dict]] = []
    for idx, detections in enumerate(frames_detections):
        now[0] = clock_base + idx / fps
        for hit in engine.evaluate(rules, detections):
            hits.append((idx / fps, hit.rule_type, hit.detail))
    return hits


def replay_trial_source(video_path: Path, pack_dir: Path,
                        camera_id: str) -> list[tuple[float, str, dict]]:
    """用当前（mock）检测器逐帧检测试跑源，并重放包内规则。"""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"无法打开试跑源: {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 12.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        detector = MockDetector()
        frames_detections: list[list] = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames_detections.append(detector.detect(frame))
    finally:
        cap.release()
    rules = make_rule_stubs(pack_dir, camera_id, width, height)
    return run_rule_engine(frames_detections, rules, fps)


def check_scene_replay(pack_dir: Path, scene: dict, events: list[dict],
                       label: str) -> list[str]:
    """重放合同：试跑源经检测器 + 规则引擎重放，命中与声明事件一一对应。"""
    trial = pack_dir / scene["trial_source"]
    hits = replay_trial_source(trial, pack_dir, scene["camera"])
    if len(hits) != len(events):
        return [f"{label}: 重放命中 {len(hits)} 次，与声明的 {len(events)} 条事件不一致"]
    errors: list[str] = []
    for i, (hit, event) in enumerate(zip(hits, events)):
        if abs(hit[0] - float(event["at_sec"])) > REPLAY_TIME_TOLERANCE:
            errors.append(
                f"{label}: 第 {i + 1} 次命中 at={hit[0]:.2f}s "
                f"与声明 {event['at_sec']}s 偏差超过 {REPLAY_TIME_TOLERANCE}s")
    return errors


# ---------- fast-food 场景合同 ----------

SCENE_ASSET_KEYS = ("input_preview", "result_preview", "poster", "events", "trial_source")


def check_fast_food_scenes(data: dict) -> list[str]:
    errors: list[str] = []
    pack_dir = PACKS_DIR / "fast-food"
    scenes = ((data.get("experience") or {}).get("scenes"))
    if not isinstance(scenes, list):
        return ["fast-food: 缺少 experience.scenes"]
    by_id = {s.get("id"): s for s in scenes if isinstance(s, dict)}
    for scene_id in FAST_FOOD_SCENES:
        label = f"fast-food/{scene_id}"
        scene = by_id.get(scene_id)
        if scene is None:
            errors.append(f"{label}: 场景未声明")
            continue
        for key in SCENE_ASSET_KEYS:
            if not (scene.get(key) or "").strip():
                errors.append(f"{label}: 缺少 {key}")
        if errors and not all(scene.get(k) for k in SCENE_ASSET_KEYS):
            continue
        camera_id = scene.get("camera")
        stubs = make_rule_stubs(pack_dir, camera_id, 640, 360)
        if len(stubs) != 1:
            errors.append(f"{label}: 机位 {camera_id} 应恰好挂 1 条规则，实际 {len(stubs)}")
            continue
        # 媒体合同：原始画面（trial_source）与结果演示
        media_errors, input_info = check_video(pack_dir / scene["trial_source"], f"{label} trial")
        errors.extend(media_errors)
        result_errors, result_info = check_video(
            pack_dir / scene["result_preview"], f"{label} result")
        errors.extend(result_errors)
        if input_info and result_info \
                and abs(input_info["duration"] - result_info["duration"]) > 0.2:
            errors.append(f"{label}: 原始与结果视频时长不一致")
        if input_info:
            errors.extend(check_poster(pack_dir / scene["poster"],
                                       input_info["width"], input_info["height"], label))
            # 事件时间线 + 重放
            try:
                events = load_events(pack_dir / scene["events"])
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{label}: {exc}")
                continue
            errors.extend(check_events(events, input_info["duration"], stubs[0].type, label))
            errors.extend(check_scene_replay(pack_dir, scene, events, label))
    return errors


def collect_errors() -> list[str]:
    errors: list[str] = []
    for pack_id in BUILTIN_PACKS:
        data = load_pack_yaml(pack_id)
        errors.extend(check_presentation(pack_id, data))
        if pack_id == "fast-food":
            errors.extend(check_fast_food_scenes(data))
        else:
            errors.extend(check_legacy_degrade(pack_id, data))
    return errors


def main() -> int:
    errors = collect_errors()
    if errors:
        print(f"体验资产合同校验失败（{len(errors)} 项）：")
        for err in errors:
            print(f"  ✗ {err}")
        return 1
    print("体验资产合同校验通过：四包文案完整，fast-food 四路资产与重放结果一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
