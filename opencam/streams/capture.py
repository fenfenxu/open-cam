"""流接入：CaptureWorker 线程 + 环形帧缓冲。

- FileSource：视频文件循环播放，按文件帧率限速模拟实时流。
- RTSPSource：网络流，读取失败时指数退避重连。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CaptureWorker:
    """在后台线程中持续读帧，最新若干帧保存在 deque 里。"""

    def __init__(self, uri: str, buffer_size: int = 64):
        self.uri = uri
        self._frames: deque[np.ndarray] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None
        # 最近一次读帧成功的单调时间，用于判断流是否活着
        self.last_frame_at: float = 0.0

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"capture-{self.uri[:32]}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._release()

    # ---- 对外接口 ----

    def latest_frame(self) -> Optional[np.ndarray]:
        """返回最新一帧（copy），没有帧时返回 None。"""
        with self._lock:
            if not self._frames:
                return None
            return self._frames[-1].copy()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive()) and not self._stop.is_set()

    # ---- 内部 ----

    def _open(self) -> bool:
        self._release()
        cap = cv2.VideoCapture(self.uri)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        return True

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _push(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frames.append(frame)
        self.last_frame_at = time.monotonic()

    def _read(self) -> Optional[np.ndarray]:
        """读一帧，失败返回 None。子类可重写。"""
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def _run(self) -> None:
        raise NotImplementedError


class FileSource(CaptureWorker):
    """视频文件源：播完循环，按文件 fps 限速模拟实时。"""

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._open():
                logger.error("无法打开视频文件: %s", self.uri)
                return
            fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
            interval = 1.0 / max(fps, 1.0)
            logger.info("文件源已打开 %s (fps=%.1f)", self.uri, fps)
            while not self._stop.is_set():
                t0 = time.monotonic()
                frame = self._read()
                if frame is None:
                    break  # 播完，外层循环重新打开实现循环播放
                self._push(frame)
                # 限速：读帧耗时之外的剩余时间睡掉
                elapsed = time.monotonic() - t0
                wait = interval - elapsed
                if wait > 0:
                    self._stop.wait(wait)
        self._release()


class RTSPSource(CaptureWorker):
    """RTSP 网络流：打开/读取失败时指数退避重连。"""

    MAX_BACKOFF = 30.0

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self._open():
                logger.warning("RTSP 打开失败 %s，%.1fs 后重试", self.uri, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)
                continue
            backoff = 1.0
            logger.info("RTSP 已连接 %s", self.uri)
            fail_count = 0
            while not self._stop.is_set():
                frame = self._read()
                if frame is None:
                    fail_count += 1
                    if fail_count >= 10:  # 连续读失败判定断线
                        logger.warning("RTSP 读取连续失败，重连 %s", self.uri)
                        break
                    self._stop.wait(0.1)
                    continue
                fail_count = 0
                self._push(frame)
        self._release()


def make_worker(source_type: str, uri: str) -> CaptureWorker:
    if source_type == "file":
        return FileSource(uri)
    if source_type == "rtsp":
        return RTSPSource(uri)
    raise ValueError(f"未知 source_type: {source_type}")
