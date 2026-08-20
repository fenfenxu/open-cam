"""分析流水线：capture → detect → rules → event → vlm 队列。

每个运行中的摄像头一条 PipelineWorker 线程，按 detect_fps 从帧缓冲取帧。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2

from .clip import annotate_frame
from .config import settings
from .db import get_session
from .detection.detector import build_detector
from .detection.escalate import Escalator, find_open_todo
from .detection.rules import RuleEngine
from .detection.vlm import vlm_reviewer
from .models import (CAMERA_RUNNING, EVENT_LOGGED, EVENT_OPEN, INTENT_OBSERVE,
                      Camera, CameraBinding, Event, EventAction, ModelBinding,
                      ModelVersion, PipelineStage, Rule, VLM_PENDING, VLM_SKIPPED,
                      default_intent)
from .notify import notifier
from .runtime import RuntimePlan, RuntimeResolutionError, resolve_runtime_plan
from .streams.manager import camera_manager

logger = logging.getLogger(__name__)

_default_escalator = Escalator()


def persist_hit(session, camera_id: int, rule: Rule, hit, snapshot_path: str | None,
                source_offset: float | None = None, *,
                escalator: Escalator | None = None,
                now: float | None = None,
                runtime_plan: RuntimePlan | None = None) -> Event | None:
    """按 intent/策略写库或折叠。返回新建或升格后的待办 Event；观察/未过关可返回 logged 或 None。"""
    escalator = escalator or _default_escalator
    now = time.time() if now is None else now
    intent = rule.intent or default_intent(rule.type)
    if intent == INTENT_OBSERVE:
        event = _insert_event(
            session, camera_id, hit, snapshot_path, source_offset, intent,
            needs_action=False, status=EVENT_LOGGED, ts=now,
            runtime_plan=runtime_plan,
            rule_capabilities=getattr(rule, "capabilities", []))
        return event

    escalator.note_hit(rule.id, now)
    decision = escalator.decide(session, rule, camera_id, now)
    if not decision.write_logged and not decision.open_todo:
        return None
    event = None
    if decision.write_logged and not decision.open_todo:
        event = _insert_event(
            session, camera_id, hit, snapshot_path, source_offset, intent,
            needs_action=False, status=EVENT_LOGGED, ts=now,
            runtime_plan=runtime_plan,
            rule_capabilities=getattr(rule, "capabilities", []))
    if decision.fold:
        existing = find_open_todo(session, camera_id, rule.id)
        if existing is not None:
            _fold_todo(session, existing, snapshot_path, now)
            return existing
    if decision.open_todo:
        if event is None:
            if decision.write_logged:
                event = _insert_event(
                    session, camera_id, hit, snapshot_path, source_offset, intent,
                    needs_action=False, status=EVENT_LOGGED, ts=now,
                    runtime_plan=runtime_plan,
                    rule_capabilities=getattr(rule, "capabilities", []))
            else:
                event = _insert_event(
                    session, camera_id, hit, snapshot_path, source_offset, intent,
                    needs_action=True, status=EVENT_OPEN, ts=now,
                    runtime_plan=runtime_plan,
                    rule_capabilities=getattr(rule, "capabilities", []))
        event.needs_action = True
        event.status = EVENT_OPEN
        if event.vlm_status == VLM_SKIPPED:
            event.vlm_status = VLM_PENDING
        session.commit()
        return event
    return event


def _insert_event(session, camera_id: int, hit, snapshot_path: str | None,
                  source_offset: float | None, intent: str, *,
                  needs_action: bool, status: str, ts: float,
                  runtime_plan: RuntimePlan | None = None,
                  rule_capabilities: list[str] | None = None) -> Event:
    stage = (runtime_plan.stage_for(rule_capabilities or [])
             if runtime_plan else None)
    event = Event(
        camera_id=camera_id,
        rule_id=hit.rule_id,
        type=hit.rule_type,
        confidence=hit.confidence,
        ts=ts,
        snapshot_path=snapshot_path,
        source_offset=source_offset,
        detail=hit.detail,
        intent=intent,
        needs_action=needs_action,
        status=status,
        repeat_count=1,
        vlm_status=VLM_SKIPPED if not needs_action else VLM_PENDING,
        analysis_profile_version=(runtime_plan.analysis_profile_version
                                  if runtime_plan else None),
        pipeline_stage=stage.key if stage else None,
        model_version_id=stage.model_version_id if stage else None,
        artifact_digest=stage.artifact_digest if stage else None,
    )
    session.add(event)
    session.commit()
    return event


def _fold_todo(session, existing: Event, snapshot_path: str | None, now: float) -> None:
    existing.repeat_count = int(existing.repeat_count or 1) + 1
    detail = dict(existing.detail or {})
    if snapshot_path:
        detail["last_snapshot_path"] = snapshot_path
        existing.detail = detail
    session.add(EventAction(
        event_id=existing.id, action="repeat", actor="agent",
        payload={"count": existing.repeat_count}, ts=now))
    session.commit()


class PipelineWorker:
    """单摄像头的分析线程。"""

    def __init__(self, camera_id: int, detector=None,
                 runtime_plan: RuntimePlan | None = None):
        self.camera_id = camera_id
        self.runtime_plan = runtime_plan
        # 共享传入的检测器（多路摄像头共用一个模型）；未传则自己构建
        self._detector = detector or build_detector(
            runtime_plan.model_path if runtime_plan else None)
        self._engine = RuleEngine()
        self._escalator = Escalator()
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
        sample = camera_manager.latest_sample(self.camera_id)
        if sample is None:
            return
        frame = sample.frame
        detections = self._detector.detect(frame) or []

        session = get_session()
        try:
            rules = session.query(Rule).filter_by(
                camera_id=self.camera_id, enabled=True).all()
            hits = self._engine.evaluate(rules, detections)
            match_ids = {h.rule_id for h in self._engine.last_matches}
            now = time.time()
            for rule in rules:
                if rule.id in match_ids:
                    self._escalator.note_hit(rule.id, now)
                else:
                    self._escalator.note_miss(rule.id)
            by_id = {r.id: r for r in rules}
            for hit in hits:
                rule = by_id.get(hit.rule_id)
                if rule is None:
                    continue
                existing = find_open_todo(session, self.camera_id, rule.id)
                snapshot = self._save_snapshot(frame, sample.offset)
                event = persist_hit(
                    session, self.camera_id, rule, hit,
                    str(snapshot) if snapshot else None, sample.offset,
                    escalator=self._escalator, now=now,
                    runtime_plan=self.runtime_plan)
                if event is None:
                    continue
                logger.info("事件落库: id=%d camera=%d type=%s offset=%s action=%s",
                            event.id, event.camera_id, event.type,
                            event.source_offset, event.needs_action)
                folded = existing is not None and event.id == existing.id
                if event.needs_action and not folded:
                    vlm_reviewer.submit(event.id)
                    notifier.submit(event.id)
        finally:
            session.close()

    def _save_snapshot(self, frame, source_offset: float | None = None) -> Path | None:
        """保存快照，返回相对数据目录的路径（snapshots/xxx.jpg）。

        库里只存相对路径：数据目录整体搬迁/升级后仍然有效。
        读取侧统一走 config.resolve_snapshot_path。
        """
        try:
            snap_dir = settings.snapshot_dir
            snap_dir.mkdir(parents=True, exist_ok=True)
            name = f"cam{self.camera_id}_{int(time.time() * 1000)}.jpg"
            cv2.imwrite(str(snap_dir / name), annotate_frame(frame, source_offset))
            return Path("snapshots") / name
        except Exception:  # noqa: BLE001 快照失败不影响事件入库
            logger.exception("保存快照失败")
            return None


class PipelineManager:
    """pipeline 线程注册表。"""

    def __init__(self):
        self._workers: dict[int, PipelineWorker] = {}
        self._lock = threading.Lock()
        self._errors: dict[int, str] = {}

    def start(self, camera_id: int, runtime_plan: RuntimePlan | None = None) -> None:
        self.stop(camera_id)
        worker = PipelineWorker(camera_id, runtime_plan=runtime_plan)
        worker.start()
        with self._lock:
            self._workers[camera_id] = worker
            self._errors.pop(camera_id, None)

    def stop(self, camera_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def set_error(self, camera_id: int, reason: str) -> None:
        with self._lock:
            self._errors[camera_id] = reason

    def runtime_status(self, camera_id: int) -> tuple[str, str | None]:
        with self._lock:
            error = self._errors.get(camera_id)
            worker = self._workers.get(camera_id)
        if error:
            return "unavailable", error
        if worker is None:
            return "not_running", None
        plan = worker.runtime_plan
        return (plan.runtime_status, plan.reason) if plan else ("ready", None)

    def runtime_plan(self, camera_id: int) -> RuntimePlan | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.runtime_plan if worker else None

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
        try:
            runtime_plan = resolve_runtime_plan(session, camera_id)
        except RuntimeResolutionError as exc:
            camera.status = "error"
            session.commit()
            pipeline_manager.set_error(camera_id, str(exc))
            raise
        try:
            camera_manager.start(camera_id, camera.source_type, camera.source_uri)
            pipeline_manager.start(camera_id, runtime_plan=runtime_plan)
            camera.status = CAMERA_RUNNING
            session.commit()
        except Exception as exc:
            # 采集或推理初始化失败也要留下可读的运行时健康原因，避免只看到
            # 一个没有上下文的 500；已启动的采集线程同步清理。
            pipeline_manager.stop(camera_id)
            camera_manager.stop(camera_id)
            camera.status = "error"
            pipeline_manager.set_error(camera_id, str(exc))
            session.commit()
            raise
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


def restart_cameras_for_model_change(session, model_version_id: int) -> None:
    """模型部署/回滚后重启受影响的运行中摄像头，加载新 RuntimePlan。"""
    version = session.get(ModelVersion, model_version_id)
    if version is None:
        return
    stage_ids = {row.id for row in session.query(PipelineStage).filter(
        (PipelineStage.model_version_id == model_version_id) |
        (PipelineStage.model_slot_key == version.slot_key)).all()}
    stage_ids.update(row.target_id for row in session.query(ModelBinding).filter_by(
        model_asset_id=version.model_asset_id, target_type="pipeline_stage",
        enabled=True).all() if row.target_id is not None)
    camera_ids = [binding.camera_id for binding in session.query(CameraBinding).filter(
        CameraBinding.analysis_profile_id.in_(
            session.query(PipelineStage.profile_id).filter(PipelineStage.id.in_(stage_ids))
        )).all()] if stage_ids else []
    for camera_id in dict.fromkeys(camera_ids):
        camera = session.get(Camera, camera_id)
        if camera is not None and camera.status == CAMERA_RUNNING:
            start_camera(camera_id)
