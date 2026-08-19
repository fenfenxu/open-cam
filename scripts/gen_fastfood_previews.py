"""生成快餐店方案包 4 路占位演示 mp4（OpenCV 画相对坐标 + 英文 id）。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

W, H = 640, 360
FPS = 5
N_FRAMES = 15

OUT_DIR = Path(__file__).resolve().parents[1] / "packs" / "fast-food" / "cameras"

# 与 packs/fast-food/rules/*.yaml 默认相对坐标一致
CAMERAS: dict[str, dict] = {
    "door": {"line": [[0.3, 0.5], [0.7, 0.5]]},
    "counter": {
        "polygon": [[0.15, 0.4], [0.55, 0.4], [0.55, 1.0], [0.15, 1.0]],
    },
    "kitchen": {
        "polygon": [[0.7, 0.2], [1.0, 0.2], [1.0, 1.0], [0.7, 1.0]],
    },
    "hall": {
        "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        "faint": True,
    },
}


def _px(pt: list[float]) -> tuple[int, int]:
    return int(pt[0] * W), int(pt[1] * H)


def _draw(frame: np.ndarray, cam_id: str, spec: dict) -> None:
    if "line" in spec:
        p1, p2 = _px(spec["line"][0]), _px(spec["line"][1])
        cv2.line(frame, p1, p2, (0, 255, 255), 2)
    if "polygon" in spec:
        pts = np.array([_px(p) for p in spec["polygon"]], dtype=np.int32)
        color = (80, 80, 80) if spec.get("faint") else (0, 200, 0)
        thickness = 1 if spec.get("faint") else 2
        cv2.polylines(frame, [pts], True, color, thickness)
    cv2.putText(frame, cam_id, (16, 36), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (255, 255, 255), 2, cv2.LINE_AA)


def generate() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    for cam_id, spec in CAMERAS.items():
        path = OUT_DIR / f"{cam_id}.mp4"
        writer = cv2.VideoWriter(str(path), fourcc, FPS, (W, H))
        if not writer.isOpened():
            raise SystemExit(f"无法写入 {path}")
        for _ in range(N_FRAMES):
            frame = np.zeros((H, W, 3), dtype=np.uint8)
            frame[:] = (40, 40, 40)
            _draw(frame, cam_id, spec)
            writer.write(frame)
        writer.release()
        print(path)


if __name__ == "__main__":
    generate()
