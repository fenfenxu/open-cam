#!/usr/bin/env python3
"""生成快餐店方案包四路场景的体验资产（可重复执行，幂等覆盖）。

每个场景产出：
- cameras/{camera}.mp4              H.264/yuv420p 原始画面（同时是 trial_source 与 input_preview）
- experience/{scene}-result.mp4     叠加检测框 / ROI / 触发状态的结果演示
- experience/{scene}.jpg            海报（取首个触发时刻的结果画面）
- experience/{scene}.events.json    事件时间线（脚本化轨迹经 RuleEngine 真实重放得出）
另产出 experience/cover.jpg（四路海报 2x2 拼图）。

人形用纯白竖直 sprite 渲染，与 MockDetector 的内容识别约定一致——真实 YOLO 不保证
识别合成图形（见 tests/test_pipeline_e2e.py），mock 模式下规则链路由画面内容真实驱动。

依赖系统 ffmpeg（libx264）。用法：uv run python scripts/gen_fastfood_previews.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opencam.detection.detector import Detection  # noqa: E402
from opencam.detection.rules import point_in_polygon  # noqa: E402
from opencam.models import default_intent  # noqa: E402
from opencam.packs.apply import scale_params  # noqa: E402
from opencam.packs.manifest import load_rule_templates  # noqa: E402

from check_pack_experience import NIGHT_BASE, run_rule_engine  # noqa: E402

W, H = 640, 360
FPS = 12

PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "fast-food"
CAMERAS_DIR = PACK_DIR / "cameras"
EXPERIENCE_DIR = PACK_DIR / "experience"

WHITE = (255, 255, 255)
BOX_GREEN = (80, 220, 80)
ROI_COLOR = (196, 160, 53)
LINE_COLOR = (0, 215, 255)
ALERT_RED = (60, 60, 230)


@dataclass
class Actor:
    """一个合成人形：sprite 尺寸 + 脚底关键帧路径 [(t, x, y)]（像素，线性插值）。

    首帧之前不出现，末帧之后停在原地。track label 按（出现时间, x）排序编号。
    """

    width: int
    body_h: int
    keys: list[tuple[float, float, float]]

    def pos_at(self, t: float) -> tuple[float, float] | None:
        if t < self.keys[0][0]:
            return None
        for (t0, x0, y0), (t1, x1, y1) in zip(self.keys, self.keys[1:]):
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        return self.keys[-1][1], self.keys[-1][2]

    def bbox_at(self, t: float) -> tuple[float, float, float, float] | None:
        """与 cv2 填充绘制 / boundingRect 一致的包围框（底边中点 = 脚底）。"""
        pos = self.pos_at(t)
        if pos is None:
            return None
        x, y = pos
        r = int(self.width * 0.42)
        return (x - self.width / 2, y - self.body_h - r,
                x + self.width / 2 + 1, y + 1)


def _vgrad(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    col = np.linspace(top, bottom, H).reshape(H, 1, 3)
    return np.repeat(col, W, axis=1).astype(np.uint8)


def _bg_door() -> np.ndarray:
    frame = _vgrad((42, 38, 35), (96, 86, 76))
    # 门洞与门框
    cv2.rectangle(frame, (243, 0), (397, 126), (30, 28, 26), -1)
    cv2.rectangle(frame, (243, 0), (397, 126), (150, 140, 128), 2)
    return frame


def _bg_counter() -> np.ndarray:
    frame = _vgrad((46, 40, 36), (98, 88, 78))
    # 右侧点餐柜台与上方菜单牌
    cv2.rectangle(frame, (397, 108), (640, 259), (75, 95, 115), -1)
    cv2.rectangle(frame, (410, 40), (610, 90), (60, 55, 50), -1)
    cv2.rectangle(frame, (410, 40), (610, 90), (140, 130, 120), 2)
    return frame


def _bg_kitchen() -> np.ndarray:
    frame = _vgrad((44, 42, 40), (88, 84, 80))
    # 右侧后厨区域（设备台面）
    cv2.rectangle(frame, (448, 72), (640, 360), (85, 76, 70), -1)
    cv2.rectangle(frame, (470, 120), (620, 180), (70, 64, 60), -1)
    cv2.rectangle(frame, (470, 210), (620, 270), (70, 64, 60), -1)
    return frame


def _bg_hall() -> np.ndarray:
    frame = _vgrad((40, 36, 34), (92, 82, 74))
    # 店内餐座（椭圆桌面，横向不构成人形）
    for cx, cy in ((150, 250), (330, 220), (500, 260)):
        cv2.ellipse(frame, (cx, cy), (70, 22), 0, 0, 360, (75, 68, 60), -1)
        cv2.ellipse(frame, (cx, cy), (70, 22), 0, 0, 360, (120, 110, 100), 1)
    return frame


def _draw_sprite(frame: np.ndarray, actor: Actor, x: float, y: float) -> None:
    xi, yi = int(round(x)), int(round(y))
    hw = actor.width // 2
    cv2.rectangle(frame, (xi - hw, yi - actor.body_h), (xi + hw, yi), WHITE, -1)
    cv2.circle(frame, (xi, yi - actor.body_h), int(actor.width * 0.42), WHITE, -1)


def _event_text(scene_id: str, detail: dict) -> tuple[str, str]:
    """命中明细 -> 事件时间线的业务文案。"""
    if scene_id == "door-flow":
        if detail.get("direction") == "in":
            return "检测到 1 人进店", "记录进店客流 +1"
        return "检测到 1 人出店", "记录出店客流 +1"
    if scene_id == "queue-count":
        return f"点餐区人数达到 {detail.get('count')} 人", "打开待办：点餐区排队超员"
    if scene_id == "kitchen-intrusion":
        return "有人进入后厨区域", "打开待办：后厨闯入"
    return "闭店时段检测到店内有人", "打开待办：闭店后入侵"


@dataclass
class Scene:
    scene_id: str
    camera: str
    duration: float
    actors: list[Actor]
    bg: object  # Callable[[], np.ndarray]


SCENES: list[Scene] = [
    Scene("door-flow", "door", 12.0, [
        # 进店：从门洞走向镜头，穿越门口计数线
        Actor(58, 110, [(0.8, 269, 162), (4.0, 269, 388)]),
        # 出店：反向穿越
        Actor(58, 110, [(5.5, 371, 372), (8.9, 371, 134)]),
    ], _bg_door),
    Scene("queue-count", "counter", 14.0, [
        # 4 人已在点餐区内排队，第 5 人到达即超员（阈值 5）
        Actor(40, 70, [(0.0, 110, 250)]),
        Actor(40, 70, [(0.0, 165, 250)]),
        Actor(40, 70, [(0.0, 220, 250)]),
        Actor(40, 70, [(0.0, 275, 250)]),
        Actor(40, 70, [(3.0, 700, 340), (7.4, 330, 250)]),
    ], _bg_counter),
    Scene("kitchen-intrusion", "kitchen", 10.0, [
        # 有人从左侧走入后厨区域
        Actor(52, 90, [(0.5, 40, 300), (7.0, 590, 300)]),
    ], _bg_kitchen),
    Scene("after-hours", "hall", 10.0, [
        # 闭店时段（重放时钟固定 23:00）店内出现人员
        Actor(52, 90, [(1.0, 30, 330), (9.5, 620, 330)]),
    ], _bg_hall),
]


def _scene_rule(scene: Scene):
    templates = [t for t in load_rule_templates(PACK_DIR) if t.camera == scene.camera]
    if len(templates) != 1:
        raise SystemExit(f"{scene.scene_id}: 机位 {scene.camera} 应恰好挂 1 条规则")
    return templates[0]


def _track_labels(scene: Scene) -> dict[int, int]:
    """actor 下标 -> 展示用 track 号（按出现时间、x 排序，与 MockDetector 一致）。"""
    order = sorted(range(len(scene.actors)),
                   key=lambda i: (scene.actors[i].keys[0][0], scene.actors[i].keys[0][1]))
    return {actor_idx: n for n, actor_idx in enumerate(order, start=1)}


def _frame_detections(scene: Scene, t: float) -> list[Detection]:
    labels = _track_labels(scene)
    detections = []
    for i, actor in enumerate(scene.actors):
        bbox = actor.bbox_at(t)
        if bbox is not None:
            detections.append(Detection(
                bbox=bbox, confidence=0.9, class_id=0,
                class_name="person", track_id=labels[i]))
    return detections


def _render_input(scene: Scene, t: float) -> np.ndarray:
    frame = scene.bg()
    for actor in scene.actors:
        pos = actor.pos_at(t)
        if pos is not None:
            _draw_sprite(frame, actor, pos[0], pos[1])
    return frame


def _render_result(scene: Scene, t: float, detections: list[Detection],
                   params: dict, fire_times: list[float],
                   counters: dict[str, int]) -> np.ndarray:
    frame = _render_input(scene, t)
    # ROI / 计数线
    polygon = params.get("polygon")
    if polygon:
        pts = np.array(polygon, dtype=np.int32).reshape(-1, 1, 2)
        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], ROI_COLOR)
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)
        cv2.polylines(frame, [pts], True, ROI_COLOR, 2)
    line = params.get("line")
    if line:
        p1 = tuple(int(v) for v in line[0])
        p2 = tuple(int(v) for v in line[1])
        cv2.line(frame, p1, p2, LINE_COLOR, 2)
    # 目标框与 track 号
    for det in detections:
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_GREEN, 2)
        cv2.putText(frame, f"P{det.track_id}", (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_GREEN, 1, cv2.LINE_AA)
    # 状态条
    if scene.scene_id == "door-flow":
        status = f"IN {counters.get('in', 0)}  OUT {counters.get('out', 0)}"
    elif scene.scene_id == "queue-count":
        in_zone = sum(1 for d in detections
                      if point_in_polygon(d.bottom_center, params["polygon"]))
        status = f"QUEUE {in_zone}/{int(params.get('threshold', 5))}"
    else:
        status = f"PERSONS {len(detections)}"
    cv2.rectangle(frame, (0, 0), (W, 26), (30, 28, 26), -1)
    cv2.putText(frame, f"{scene.scene_id}  {status}", (8, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    # 触发状态：命中后 1 秒内红框 + 横幅
    if any(0 <= t - ft < 1.0 for ft in fire_times):
        cv2.rectangle(frame, (0, 0), (W - 1, H - 1), ALERT_RED, 6)
        cv2.rectangle(frame, (W - 150, 34), (W - 8, 60), ALERT_RED, -1)
        cv2.putText(frame, "EVENT FIRED", (W - 144, 53),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
    return frame


def _encode(frames: list[np.ndarray], path: Path) -> None:
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
           "-preset", "medium", "-movflags", "+faststart", "-an", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stderr is not None
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg 编码失败 {path}:\n{err}")


def generate_scene(scene: Scene) -> Path:
    """生成单场景全部资产，返回海报路径（供封面拼图）。"""
    tpl = _scene_rule(scene)
    params = scale_params(tpl.params, W, H)
    rule = {"id": 1, "type": tpl.type, "params": params,
            "cooldown": tpl.cooldown, "enabled": True}
    from types import SimpleNamespace
    rule_stub = SimpleNamespace(**rule)

    n_frames = int(scene.duration * FPS)
    frames_detections = [_frame_detections(scene, idx / FPS) for idx in range(n_frames)]
    hits = run_rule_engine(frames_detections, [rule_stub], FPS)
    if not hits:
        raise SystemExit(f"{scene.scene_id}: 脚本化轨迹未触发任何规则命中")

    # 事件时间线：由真实重放结果生成，而不是手写时刻
    events = []
    for t, _rule_type, detail in hits:
        title, result = _event_text(scene.scene_id, detail)
        events.append({"at_sec": round(t, 1), "title": title,
                       "result": result, "intent": default_intent(tpl.type)})
    events_path = EXPERIENCE_DIR / f"{scene.scene_id}.events.json"
    events_path.write_text(json.dumps({"events": events}, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")

    fire_times = [t for t, _, _ in hits]
    input_frames: list[np.ndarray] = []
    result_frames: list[np.ndarray] = []
    counters: dict[str, int] = {}
    hit_idx = 0
    poster_frame: np.ndarray | None = None
    for idx in range(n_frames):
        t = idx / FPS
        while hit_idx < len(hits) and hits[hit_idx][0] <= t + 1e-9:
            direction = hits[hit_idx][2].get("direction")
            if direction:
                counters[direction] = counters.get(direction, 0) + 1
            hit_idx += 1
        input_frames.append(_render_input(scene, t))
        result = _render_result(scene, t, frames_detections[idx], params,
                                fire_times, counters)
        if poster_frame is None and fire_times and t >= fire_times[0]:
            poster_frame = result.copy()
        result_frames.append(result)

    _encode(input_frames, CAMERAS_DIR / f"{scene.camera}.mp4")
    _encode(result_frames, EXPERIENCE_DIR / f"{scene.scene_id}-result.mp4")
    poster_path = EXPERIENCE_DIR / f"{scene.scene_id}.jpg"
    cv2.imwrite(str(poster_path),
                poster_frame if poster_frame is not None else result_frames[0],
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"{scene.scene_id}: {n_frames} 帧, {len(events)} 条事件 -> {events_path.name}")
    return poster_path


def generate_cover(poster_paths: list[Path]) -> None:
    tiles = [cv2.resize(cv2.imread(str(p)), (W // 2, H // 2)) for p in poster_paths]
    cover = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    cv2.imwrite(str(EXPERIENCE_DIR / "cover.jpg"), cover,
                [cv2.IMWRITE_JPEG_QUALITY, 88])


def main() -> None:
    CAMERAS_DIR.mkdir(parents=True, exist_ok=True)
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    posters = [generate_scene(scene) for scene in SCENES]
    generate_cover(posters)
    print("完成：4 路原始/结果视频、海报、事件时间线与封面已生成。")


if __name__ == "__main__":
    main()
