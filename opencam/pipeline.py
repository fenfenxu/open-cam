"""分析流水线：capture → detect → rules → event → vlm 队列。

每个运行中的摄像头一条 PipelineWorker 线程，按 detect_fps 从帧缓冲取帧。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2

from .config import settings
from .db import get_session
from .detection.detector import build_detector
from .detection.rules import RuleEngine, RuleHit
from .detection.vlm import vlm_reviewer
from .models import CAMERA_RUNNING, Camera, Event, Rule
from .streams.manager import camera_manager
from .training.classifier import classify_region
from .training.frames import crop_frame

logger = logging.getLogger(__name__)


class PipelineWorker:
    """单摄像头的分析线程。"""

    def __init__(self, camera_id: int, detector=None):
        self.camera_id = camera_id
        # 共享传入的检测器（多路摄像头共用一个模型）；未传则自己构建
        self._detector = detector or build_detector()
        self._engine = RuleEngine()
        # state_classify 规则的持续状态：rule_id -> {"since": float|None,
        # "last_fired": float}
        self._state_rules: dict[int, dict] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"pipeline-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = 1.0 / max(settings.detect_fps, 0.1)
        logger.info("摄像头 %d 分析流水线已启动 (detect_fps=%.1f)",
                    self.camera_id, settings.detect_fps)
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._tick()
            except Exception:  # noqa: BLE001 单帧失败不能搞死流水线
                logger.exception("摄像头 %d 分析循环异常", self.camera_id)
            elapsed = time.monotonic() - t0
            self._stop.wait(max(interval - elapsed, 0.01))

    def _tick(self) -> None:
        frame = camera_manager.latest_frame(self.camera_id)
        if frame is None:
            return

        session = get_session()
        try:
            rules = session.query(Rule).filter_by(
                camera_id=self.camera_id, enabled=True).all()
            # 分流：状态分类规则走定制小模型，其余走 YOLO 检测 + 规则引擎
            state_rules = [r for r in rules if r.type == "state_classify"]
            detect_rules = [r for r in rules if r.type != "state_classify"]
        finally:
            session.close()

        hits: list[RuleHit] = []
        for rule in state_rules:
            try:
                hit = self._eval_state_rule(rule, frame)
            except Exception:  # noqa: BLE001 单条规则失败不影响其他
                logger.exception("状态分类规则 %d 评估异常", rule.id)
                hit = None
            if hit is not None:
                hits.append(hit)
        if detect_rules:
            detections = self._detector.detect(frame)
            if detections:
                hits.extend(self._engine.evaluate(detect_rules, detections))

        session = get_session()
        try:
            for hit in hits:
                snapshot = self._save_snapshot(frame)
                event = Event(
                    camera_id=self.camera_id,
                    rule_id=hit.rule_id,
                    type=hit.rule_type,
                    confidence=hit.confidence,
                    snapshot_path=str(snapshot) if snapshot else None,
                    detail=hit.detail,
                )
                session.add(event)
                session.commit()
                logger.info("事件落库: id=%d camera=%d type=%s",
                            event.id, event.camera_id, event.type)
                vlm_reviewer.submit(event.id)
        finally:
            session.close()

    def _eval_state_rule(self, rule: Rule, frame) -> RuleHit | None:
        """状态分类规则：裁剪固定区域 → 定制小模型分类 →
        触发类别持续 duration_s 秒才告警（cooldown 去抖）。"""
        now = time.time()
        state = self._state_rules.setdefault(
            rule.id, {"since": None, "last_fired": -1e18})
        if now - state["last_fired"] < rule.cooldown:
            return None
        params = rule.params
        model_path = params.get("model_path")
        if not model_path:
            return None
        crop = crop_frame(frame, params.get("polygon") or [])
        label, conf = classify_region(
            model_path, params.get("classes") or [], crop)
        trigger = params.get("trigger_class")
        threshold = float(params.get("conf_threshold", 0.6))
        if label != trigger or conf < threshold:
            state["since"] = None  # 状态消失，重新计时
            return None
        if state["since"] is None:
            state["since"] = now
            return None
        duration = float(params.get("duration_s", 300))
        if now - state["since"] < duration:
            return None
        state["last_fired"] = now
        state["since"] = None
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=conf,
            detail={
                "object_name": params.get("object_name"),
                "state": label,
                "duration_s": duration,
                "model_id": params.get("model_id"),
            },
        )

    def _save_snapshot(self, frame) -> Path | None:
        try:
            snap_dir = settings.snapshot_dir
            snap_dir.mkdir(parents=True, exist_ok=True)
            name = f"cam{self.camera_id}_{int(time.time() * 1000)}.jpg"
            path = snap_dir / name
            cv2.imwrite(str(path), frame)
            return path
        except Exception:  # noqa: BLE001 快照失败不影响事件入库
            logger.exception("保存快照失败")
            return None


class PipelineManager:
    """pipeline 线程注册表。"""

    def __init__(self):
        self._workers: dict[int, PipelineWorker] = {}
        self._lock = threading.Lock()

    def start(self, camera_id: int) -> None:
        self.stop(camera_id)
        worker = PipelineWorker(camera_id)
        worker.start()
        with self._lock:
            self._workers[camera_id] = worker

    def stop(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()

    def is_running(self, camera_id: int) -> bool:
        with self._lock:
            return camera_id in self._workers


pipeline_manager = PipelineManager()


def start_camera(camera_id: int) -> None:
    """启动一路摄像头的采集 + 分析，并更新 DB 状态。"""
    session = get_session()
    try:
        camera = session.get(Camera, camera_id)
        if camera is None:
            raise ValueError(f"摄像头不存在: {camera_id}")
        camera_manager.start(camera_id, camera.source_type, camera.source_uri)
        pipeline_manager.start(camera_id)
        camera.status = CAMERA_RUNNING
        session.commit()
    finally:
        session.close()


def stop_camera(camera_id: int) -> None:
    """停止采集与分析，更新 DB 状态。"""
    from .models import CAMERA_STOPPED

    pipeline_manager.stop(camera_id)
    camera_manager.stop(camera_id)
    session = get_session()
    try:
        camera = session.get(Camera, camera_id)
        if camera is not None:
            camera.status = CAMERA_STOPPED
            session.commit()
    finally:
        session.close()
