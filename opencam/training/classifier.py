"""运行时状态分类器：部署后的定制小模型在分析流水线里做区域分类。

- YoloStateClassifier：ultralytics 分类模型，懒加载，全局推理锁串行化。
- MockStateClassifier：无模型环境（OPENCAM_DETECTOR=mock）下按区域
  亮度确定性分流——亮 → 最后一类（异常）、暗 → 第一类（正常），
  便于合成视频的端到端验证。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

from ..config import settings
from ..detection.detector import _INFERENCE_LOCK

logger = logging.getLogger(__name__)


class YoloStateClassifier:
    """ultralytics 分类模型包装。构造时才 import ultralytics。"""

    def __init__(self, model_path: str):
        from ultralytics import YOLO  # 懒加载

        self.model = YOLO(model_path)
        self.names = self.model.names  # {id: 类别名}
        logger.info("状态分类模型已加载: %s", model_path)

    def predict(self, crop: np.ndarray) -> tuple[str, float]:
        """返回 (类别名, 置信度)。"""
        with _INFERENCE_LOCK:
            results = self.model.predict(crop, verbose=False)
        probs = results[0].probs
        top1 = int(probs.top1)
        return str(self.names.get(top1, top1)), float(probs.top1conf.item())


class MockStateClassifier:
    """确定性伪分类器：按区域平均亮度分流（亮=异常类，暗=正常类）。"""

    def __init__(self, classes: list[str], threshold: float = 128.0):
        self._classes = classes or ["正常", "异常"]
        self._threshold = threshold

    def predict(self, crop: np.ndarray) -> tuple[str, float]:
        mean = float(crop.mean()) if crop is not None and crop.size else 0.0
        label = self._classes[-1] if mean > self._threshold else self._classes[0]
        return label, 0.9


def _use_mock() -> bool:
    return os.environ.get(
        "OPENCAM_DETECTOR", settings.detector).lower() == "mock"


class _ClassifierCache:
    """按 (模型路径, 类别签名) 缓存分类器实例，避免每帧重复加载。"""

    def __init__(self):
        self._cache: dict[tuple[str, tuple[str, ...]], object] = {}
        self._lock = threading.Lock()

    def get(self, model_path: str, classes: list[str]):
        key = (model_path, tuple(classes))
        with self._lock:
            clf = self._cache.get(key)
            if clf is None:
                clf = (MockStateClassifier(classes) if _use_mock()
                       else YoloStateClassifier(model_path))
                self._cache[key] = clf
            return clf


_classifier_cache = _ClassifierCache()


def classify_region(model_path: str, classes: list[str],
                    crop: np.ndarray) -> tuple[str, float]:
    """对区域裁剪图做状态分类，返回 (类别名, 置信度)。"""
    return _classifier_cache.get(model_path, classes).predict(crop)
