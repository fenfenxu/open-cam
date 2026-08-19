"""目标检测与跟踪。

- YoloDetector：ultralytics YOLO + ByteTrack，懒加载模型（首次实例化才加载/下载权重）。
- MockDetector：内置 mock，返回合成检测框与稳定 track id，用于无模型环境与 CI 验证链路。

通过环境变量 OPENCAM_DETECTOR=mock 或配置 detector=mock 切换。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import settings

logger = logging.getLogger(__name__)

# 全局推理锁：多个摄像头流水线并发调用 model.track 时，
# torch MPS/Metal 后端的并发提交会触发原生段错误（SIGSEGV），
# ultralytics 模型实例本身也非线程安全，因此所有推理统一串行化。
_INFERENCE_LOCK = threading.Lock()


@dataclass
class Detection:
    """单个检测结果。bbox 为 (x1, y1, x2, y2) 像素坐标。"""

    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int] = None
    extra: dict = field(default_factory=dict)

    @property
    def bottom_center(self) -> tuple[float, float]:
        """检测框底边中点，用于区域入侵判定。"""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)


class YoloDetector:
    """YOLOv8 + ByteTrack。构造时才 import ultralytics 并加载权重。"""

    def __init__(self, model_path: Optional[str] = None, conf: Optional[float] = None):
        from ultralytics import YOLO  # 懒加载：避免 import 即拉模型/拖慢测试

        from ..hardware import resolve_device

        self.model = YOLO(model_path or settings.yolo_model)
        self.conf = conf if conf is not None else settings.conf_threshold
        self.device = resolve_device(settings.device)
        # COCO 类别名
        self.names = self.model.names
        logger.info("YOLO 模型已加载: %s (device=%s)",
                    model_path or settings.yolo_model, self.device)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        with _INFERENCE_LOCK:
            results = self.model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=self.conf,
                device=self.device,
                verbose=False,
            )
        detections: list[Detection] = []
        if not results:
            return detections
        boxes = results[0].boxes
        if boxes is None:
            return detections
        for i in range(len(boxes)):
            xyxy = boxes.xyxy[i].tolist()
            cls_id = int(boxes.cls[i].item())
            track_id = None
            if boxes.id is not None:
                track_id = int(boxes.id[i].item())
            detections.append(
                Detection(
                    bbox=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                    confidence=float(boxes.conf[i].item()),
                    class_id=cls_id,
                    class_name=str(self.names.get(cls_id, cls_id)),
                    track_id=track_id,
                )
            )
        return detections


class MockDetector:
    """合成检测器：在画面中部给一个缓慢水平移动的 person 框。

    track id 恒为 1，便于 loitering / zone_intrusion 规则验证。
    画面宽度未知时按 640 估算运动范围。
    """

    def __init__(self, **_: object):
        self._tick = 0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self._tick += 1
        h, w = frame.shape[:2] if frame is not None else (480, 640)
        # 在宽度的 10%~90% 之间往返移动
        period = 40
        phase = (self._tick % period) / period
        ratio = phase * 2 if phase <= 0.5 else 2 - phase * 2
        cx = w * (0.1 + 0.8 * ratio)
        bw, bh = w * 0.12, h * 0.4
        cy = h * 0.7
        return [
            Detection(
                bbox=(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2),
                confidence=0.9,
                class_id=0,
                class_name="person",
                track_id=1,
            )
        ]


def build_detector():
    """按配置/环境变量构建检测器。OPENCAM_DETECTOR 环境变量优先。"""
    kind = os.environ.get("OPENCAM_DETECTOR", settings.detector).lower()
    if kind == "mock":
        logger.info("使用 MockDetector（OPENCAM_DETECTOR=mock）")
        return MockDetector()
    return YoloDetector()
