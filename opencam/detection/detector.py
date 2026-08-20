"""目标检测与跟踪。

- YoloDetector：ultralytics YOLO + ByteTrack，懒加载模型（首次实例化才加载/下载权重）。
- MockDetector：内容驱动的内置 mock，把画面中的白色人形立牌（方案包演示/试跑源
  使用的合成 sprite）识别为 person 并维持稳定 track id，用于无模型环境与 CI 验证链路。

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
    """内容驱动的合成检测器：把画面中的"白色人形立牌"识别为 person。

    内置方案包的演示/试跑源用纯白竖直人形 sprite 渲染（真实 YOLO 不保证识别
    合成图形，见 test_pipeline_e2e.py 的说明）。本检测器用亮度阈值 + 竖直长宽比
    识别这些 sprite，并用最近邻匹配维持稳定 track id，使 mock 模式下的规则链路
    由真实画面内容驱动。画面中没有合成人形时不返回任何检测。
    """

    # sprite 识别参数：纯白填充、竖直长宽比、尺寸占画面比例限制
    _GRAY_MIN = 220
    _ASPECT_MIN, _ASPECT_MAX = 1.3, 5.0
    _H_MIN_RATIO, _H_MAX_RATIO = 0.08, 0.75
    _W_MIN_RATIO = 0.02
    _FILL_MIN = 0.5
    _MAX_MISSES = 5

    def __init__(self, **_: object):
        self._tracks: dict[int, tuple[float, float]] = {}  # id -> bottom_center
        self._misses: dict[int, int] = {}
        self._next_id = 1

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if frame is None:
            return []
        boxes = self._find_sprites(frame)
        return self._assign_tracks(boxes, frame.shape[:2])

    @classmethod
    def _find_sprites(cls, frame: np.ndarray) -> list[tuple[float, float, float, float]]:
        """找出画面中所有人形立牌的包围框（按 x 排序保证确定性）。"""
        import cv2  # 懒加载：保持模块 import 轻量

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = (gray >= cls._GRAY_MIN).astype(np.uint8) * 255
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[float, float, float, float]] = []
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            if not (cls._H_MIN_RATIO * h <= bh <= cls._H_MAX_RATIO * h):
                continue
            if bw < cls._W_MIN_RATIO * w:
                continue
            if not (cls._ASPECT_MIN <= bh / bw <= cls._ASPECT_MAX):
                continue
            if cv2.contourArea(contour) < cls._FILL_MIN * bw * bh:
                continue
            boxes.append((float(x), float(y), float(x + bw), float(y + bh)))
        boxes.sort(key=lambda b: b[0])
        return boxes

    def _assign_tracks(self, boxes: list[tuple[float, float, float, float]],
                       shape: tuple[int, int]) -> list[Detection]:
        """按 bottom_center 最近邻贪心配对，维持跨帧稳定 track id。"""
        h, w = shape
        max_dist_sq = (0.2 * (w * w + h * h) ** 0.5) ** 2
        centers = [((b[0] + b[2]) / 2.0, b[3]) for b in boxes]
        candidates = sorted(
            ((cx - tx) ** 2 + (cy - ty) ** 2, tid, i)
            for i, (cx, cy) in enumerate(centers)
            for tid, (tx, ty) in self._tracks.items()
        )
        assigned: dict[int, int] = {}  # box index -> track id
        used_tracks: set[int] = set()
        for dist_sq, tid, i in candidates:
            if dist_sq > max_dist_sq:
                break
            if i in assigned or tid in used_tracks:
                continue
            assigned[i] = tid
            used_tracks.add(tid)

        detections: list[Detection] = []
        for i, box in enumerate(boxes):
            tid = assigned.get(i)
            if tid is None:
                tid = self._next_id
                self._next_id += 1
            self._tracks[tid] = centers[i]
            self._misses[tid] = 0
            detections.append(Detection(
                bbox=box, confidence=0.9, class_id=0,
                class_name="person", track_id=tid,
            ))

        # 未配对的旧 track 记 miss，连续多帧消失后回收
        for tid in list(self._tracks):
            if tid in used_tracks or tid in assigned.values():
                continue
            self._misses[tid] += 1
            if self._misses[tid] > self._MAX_MISSES:
                del self._tracks[tid]
                del self._misses[tid]
        return detections


def build_detector(model_path: Optional[str] = None):
    """按配置/环境变量构建检测器。OPENCAM_DETECTOR 环境变量优先。"""
    kind = os.environ.get("OPENCAM_DETECTOR", settings.detector).lower()
    if kind == "mock":
        logger.info("使用 MockDetector（OPENCAM_DETECTOR=mock）")
        return MockDetector()
    return YoloDetector(model_path=model_path)
