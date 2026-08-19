"""CameraManager：管理各摄像头 CaptureWorker 的生命周期。"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from .capture import CaptureWorker, make_worker

logger = logging.getLogger(__name__)


class CameraManager:
    """camera_id -> CaptureWorker 的注册表，线程安全。"""

    def __init__(self):
        self._workers: dict[int, CaptureWorker] = {}
        self._lock = threading.Lock()

    def start(self, camera_id: int, source_type: str, uri: str) -> CaptureWorker:
        """启动一路采集；已存在则先停再启。"""
        self.stop(camera_id)
        worker = make_worker(source_type, uri)
        worker.start()
        with self._lock:
            self._workers[camera_id] = worker
        logger.info("摄像头 %d 采集已启动 (%s)", camera_id, source_type)
        return worker

    def stop(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()
            logger.info("摄像头 %d 采集已停止", camera_id)

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.items())
            self._workers.clear()
        for cid, worker in workers:
            worker.stop()

    def get(self, camera_id: int) -> Optional[CaptureWorker]:
        with self._lock:
            return self._workers.get(camera_id)

    def latest_frame(self, camera_id: int) -> Optional[np.ndarray]:
        worker = self.get(camera_id)
        return worker.latest_frame() if worker else None

    def is_running(self, camera_id: int) -> bool:
        worker = self.get(camera_id)
        return bool(worker and worker.is_alive())


# 全局单例
camera_manager = CameraManager()
