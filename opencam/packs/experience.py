"""PackExperience：方案包单场景本机隔离试跑（深模块）。

start/inspect/stop 三个接口掩盖试跑会话的全部复杂度：来源装配
（包内演示源 / 视频库 / 运行中摄像头）、帧采集、检测、规则评估、
画面叠加、MJPEG 输出、TTL 与清理路径。

约束（计划 Task 4 / 全局验收 6.3）：

- 全局最多一个主动会话；默认 60 秒 TTL，到期自动释放。
- 不写 Camera/Rule/Event/EventAction/Video，不存快照，不调 VLM/通知；
  检测与规则命中只留在内存，经状态接口与 MJPEG 吐给前端。
- 运行中摄像头只读 camera_manager 帧缓冲，不动其采集/分析生命周期。
- 推理统一走 build_detector 构建的检测器（内部有全局推理锁）；
  检测器或体验源不可用时报 503，详情页预渲染演示不受影响。
- 试跑是效果演示：规则评估剥离 active_hours 生效时段限制，
  否则「闭店后入侵」等场景在白天永远触发不了。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Optional

import cv2
import numpy as np
from pydantic import BaseModel, Field

from ..clip import resolve_source_uri
from ..config import settings
from ..db import get_session
from ..detection.detector import build_detector
from ..detection.rules import RuleEngine
from ..models import RULE_TYPE_NAMES, Camera, Video
from ..streams.capture import CaptureWorker, FrameSample, make_worker
from ..streams.manager import camera_manager
from .apply import probe_resolution, scale_params
from .catalog import catalog
from .installer import get_pack
from .manifest import PackError

logger = logging.getLogger(__name__)

TRIAL_DEFAULT_TTL = 60.0
TRIAL_MAX_TTL = 60.0
# 已结束会话保留时长：超时后从注册表清除，inspect 变为 404
_FINISHED_KEEP_SEC = 600.0

TRIAL_RUNNING = "running"
TRIAL_STOPPED = "stopped"
TRIAL_EXPIRED = "expired"
TRIAL_ERROR = "error"

SourceKind = Literal["pack", "video", "camera"]


class TrialError(Exception):
    """试跑相关错误，status 对应 HTTP 状态码，消息面向用户。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class TrialRuleStateOut(BaseModel):
    id: str  # 稳定规则 id（模板文件 stem）
    name: str
    type: str
    type_label: str
    matched: bool = False  # 当前帧是否命中（冷却前）
    hits: int = 0  # 会话内触发次数（过冷却）
    last_hit_at: float | None = None  # 素材内秒数（无素材位置时为会话秒数）


class TrialHitOut(BaseModel):
    at_sec: float
    rule_id: str
    rule_name: str
    rule_type: str
    confidence: float
    detail: dict[str, Any] = Field(default_factory=dict)


class TrialOut(BaseModel):
    id: str
    pack_id: str
    scene_id: str
    scene_title: str
    status: str  # running / stopped / expired / error
    source_kind: str
    started_at: float
    expires_at: float
    duration_sec: float
    remaining_sec: float
    fps: float = 0.0  # 实际处理帧率
    device: str = ""
    width: int = 0
    height: int = 0
    rules: list[TrialRuleStateOut] = Field(default_factory=list)
    hits: list[TrialHitOut] = Field(default_factory=list)
    error: str | None = None
    live_url: str = ""


@dataclass
class _TrialRule:
    """一条参与试跑的规则：stub 供 RuleEngine 评估，其余为展示状态。"""

    stub: SimpleNamespace
    stable_id: str
    name: str
    type: str
    matched: bool = False
    hits: int = 0
    last_hit_at: float | None = None


@dataclass
class _SourceSpec:
    kind: SourceKind
    capture: Optional[CaptureWorker] = None  # pack/video 文件源
    camera_id: Optional[int] = None  # camera 源：只读帧缓冲
    width: int = 0
    height: int = 0


class _TrialSession:
    """一次试跑会话：runner 线程 + 内存态命中/画面。"""

    def __init__(self, *, trial_id: str, pack_id: str, scene_id: str,
                 scene_title: str, source: _SourceSpec,
                 detector: Any, rules: list[_TrialRule],
                 duration_sec: float):
        self.id = trial_id
        self.pack_id = pack_id
        self.scene_id = scene_id
        self.scene_title = scene_title
        self.source = source
        self.duration_sec = duration_sec
        self.started_at = time.time()
        self.expires_at = self.started_at + duration_sec
        self._detector = detector
        self._rules = rules
        self._engine = RuleEngine()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = TRIAL_RUNNING
        self._error: str | None = None
        self._ended_at: float | None = None
        self._hits: list[TrialHitOut] = []
        self._latest_jpeg: bytes | None = None
        self._frames = 0
        self._released = False
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----

    def begin(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"pack-trial-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止并释放资源；幂等。"""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            if self._status == TRIAL_RUNNING:
                self._status = TRIAL_STOPPED
                self._ended_at = time.time()
        self._release()

    def expire(self) -> None:
        """到期清理；幂等。"""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            if self._status == TRIAL_RUNNING:
                self._status = TRIAL_EXPIRED
                self._ended_at = time.time()
        self._release()

    def _finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            if self._status == TRIAL_RUNNING:
                self._status = status
                self._error = error
                self._ended_at = time.time()

    def _release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        if self.source.capture is not None:
            self.source.capture.stop()
            logger.info("试跑 %s 文件源已释放", self.id)

    def _run(self) -> None:
        interval = 1.0 / max(settings.detect_fps, 0.1)
        logger.info("试跑 %s 已启动（%s/%s，%.0f 秒）",
                    self.id, self.pack_id, self.scene_id, self.duration_sec)
        while not self._stop.is_set():
            if time.time() >= self.expires_at:
                self._finish(TRIAL_EXPIRED)
                break
            t0 = time.monotonic()
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 异常必须释放资源，不能杀线程
                logger.exception("试跑 %s 运行异常", self.id)
                self._finish(TRIAL_ERROR, str(exc))
                break
            self._stop.wait(max(interval - (time.monotonic() - t0), 0.01))
        self._release()

    # ---- 帧处理 ----

    def _read_sample(self) -> FrameSample | None:
        if self.source.capture is not None:
            return self.source.capture.latest_sample()
        if self.source.camera_id is not None:
            # 运行中摄像头只复用帧，不触碰其生命周期
            return camera_manager.latest_sample(self.source.camera_id)
        return None

    def _tick(self) -> None:
        sample = self._read_sample()
        if sample is None:
            return
        frame = sample.frame
        detections = self._detector.detect(frame) or []
        stubs = [r.stub for r in self._rules]
        hits = self._engine.evaluate(stubs, detections)
        matched_ids = {h.rule_id for h in self._engine.last_matches}
        at_sec = (sample.offset if sample.offset is not None
                  else time.time() - self.started_at)
        with self._lock:
            by_id = {r.stub.id: r for r in self._rules}
            for r in self._rules:
                r.matched = r.stub.id in matched_ids
            for hit in hits:
                r = by_id.get(hit.rule_id)
                if r is None:
                    continue
                r.hits += 1
                r.last_hit_at = at_sec
                self._hits.append(TrialHitOut(
                    at_sec=round(at_sec, 2),
                    rule_id=r.stable_id,
                    rule_name=r.name,
                    rule_type=r.type,
                    confidence=hit.confidence,
                    detail=hit.detail,
                ))
            self._latest_jpeg = self._render(frame, detections)
            self._frames += 1

    def _render(self, frame: np.ndarray, detections) -> bytes | None:
        """叠加规则几何、检测框与状态条（OpenCV 字体仅 ASCII）。"""
        out = frame.copy()
        for r in self._rules:
            color = (0, 0, 255) if r.matched else (0, 200, 0)
            polygon = r.stub.params.get("polygon")
            if polygon:
                pts = np.array(polygon, dtype=np.int32)
                cv2.polylines(out, [pts], True, color, 2)
            line = r.stub.params.get("line")
            if line and len(line) == 2:
                p1 = tuple(int(v) for v in line[0])
                p2 = tuple(int(v) for v in line[1])
                cv2.line(out, p1, p2, color, 2)
        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(out, f"{det.class_name} {det.confidence:.2f}",
                        (x1, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 0), 1, cv2.LINE_AA)
        h, w = out.shape[:2]
        cv2.rectangle(out, (0, 0), (w, 24), (0, 0, 0), -1)
        label = (f"TRIAL {self.scene_id} | {self.fps():.1f}fps | "
                 f"left {self.remaining_sec():.0f}s | hits {len(self._hits)}")
        cv2.putText(out, label, (6, 17), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (255, 255, 255), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(
            ".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else None

    # ---- 状态读取 ----

    def status(self) -> str:
        with self._lock:
            return self._status

    def is_running(self) -> bool:
        return self.status() == TRIAL_RUNNING

    def remaining_sec(self) -> float:
        end = self._ended_at or time.time()
        return max(0.0, self.expires_at - end)

    def fps(self) -> float:
        end = self._ended_at or time.time()
        elapsed = max(end - self.started_at, 1e-6)
        return round(self._frames / elapsed, 2)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def snapshot(self) -> TrialOut:
        with self._lock:
            rules = [
                TrialRuleStateOut(
                    id=r.stable_id, name=r.name, type=r.type,
                    type_label=RULE_TYPE_NAMES.get(r.type, r.type),
                    matched=r.matched, hits=r.hits, last_hit_at=r.last_hit_at)
                for r in self._rules
            ]
            return TrialOut(
                id=self.id,
                pack_id=self.pack_id,
                scene_id=self.scene_id,
                scene_title=self.scene_title,
                status=self._status,
                source_kind=self.source.kind,
                started_at=self.started_at,
                expires_at=self.expires_at,
                duration_sec=self.duration_sec,
                remaining_sec=self.remaining_sec(),
                fps=self.fps(),
                device=str(getattr(self._detector, "device", "mock")),
                width=self.source.width,
                height=self.source.height,
                rules=rules,
                hits=list(self._hits),
                error=self._error,
                live_url=f"/api/pack-trials/{self.id}/live.mjpg",
            )


class PackExperience:
    """试跑会话注册表：全局最多一个主动会话。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, _TrialSession] = {}

    # ---- 深模块接口 ----

    def start(self, pack_id: str, scene_id: str, *,
              source_kind: SourceKind = "pack",
              video_id: int | None = None,
              camera_id: int | None = None,
              duration_sec: float | None = None) -> TrialOut:
        with self._lock:
            self._purge_finished()
            active = self._active_locked()
            if active is not None:
                raise TrialError(
                    409, f"已有进行中的试跑（{active.scene_id}），请先停止")
        duration = duration_sec if duration_sec else TRIAL_DEFAULT_TTL
        duration = max(1.0, min(float(duration), TRIAL_MAX_TTL))

        pack = get_pack(pack_id)
        if pack is None:
            raise TrialError(404, f"方案包不存在: {pack_id}")
        scene = _find_scene(pack.manifest, scene_id)
        if scene is None:
            raise TrialError(404, f"场景不存在: {scene_id}")
        if source_kind == "pack":
            # 与详情页「可试跑」标记同一判定，灰底占位源不得进入试跑
            self._check_scene_triable(pack_id, scene_id)

        source = self._build_source(
            pack, scene, source_kind, video_id, camera_id)
        try:
            rules = self._build_rules(pack, scene.camera,
                                      source.width, source.height)
            detector = self._build_detector()
        except Exception:
            if source.capture is not None:
                source.capture.stop()
            raise

        session = _TrialSession(
            trial_id=f"trial_{uuid.uuid4().hex[:12]}",
            pack_id=pack_id,
            scene_id=scene_id,
            scene_title=scene.title,
            source=source,
            detector=detector,
            rules=rules,
            duration_sec=duration,
        )
        with self._lock:
            active = self._active_locked()
            if active is not None:
                # 与注册之间的并发窗口：后到的让路
                if source.capture is not None:
                    source.capture.stop()
                raise TrialError(
                    409, f"已有进行中的试跑（{active.scene_id}），请先停止")
            self._sessions[session.id] = session
        session.begin()
        return session.snapshot()

    def inspect(self, trial_id: str) -> TrialOut:
        session = self._get(trial_id)
        if session.is_running() and time.time() >= session.expires_at:
            session.expire()  # runner 停滞时的兜底清理
        if session.status() == TRIAL_EXPIRED:
            raise TrialError(410, "试跑已过期")
        return session.snapshot()

    def stop(self, trial_id: str) -> None:
        """停止试跑；幂等（已停止/过期/出错均为成功）。"""
        session = self._get(trial_id)
        session.stop()

    def shutdown(self) -> None:
        """服务关闭路径：释放全部会话资源。"""
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.stop()

    def mjpeg_state(self, trial_id: str) -> tuple[_TrialSession, str]:
        """MJPEG 入口检查：返回 (session, status)；不可流式时抛 TrialError。"""
        session = self._get(trial_id)
        status = session.status()
        if status == TRIAL_RUNNING and time.time() >= session.expires_at:
            session.expire()
            status = TRIAL_EXPIRED
        if status == TRIAL_EXPIRED:
            raise TrialError(410, "试跑已过期")
        if status != TRIAL_RUNNING:
            raise TrialError(409, "试跑未在运行，无法推流")
        return session, status

    # ---- 内部 ----

    def _get(self, trial_id: str) -> _TrialSession:
        with self._lock:
            session = self._sessions.get(trial_id)
        if session is None:
            raise TrialError(404, f"试跑不存在: {trial_id}")
        return session

    def _active_locked(self) -> _TrialSession | None:
        for session in self._sessions.values():
            if session.is_running():
                return session
        return None

    def _purge_finished(self) -> None:
        cutoff = time.time() - _FINISHED_KEEP_SEC
        stale = [tid for tid, s in self._sessions.items()
                 if not s.is_running() and s.started_at < cutoff]
        for tid in stale:
            del self._sessions[tid]

    def _check_scene_triable(self, pack_id: str, scene_id: str) -> None:
        try:
            detail = catalog.describe(pack_id)
        except PackError as exc:
            raise TrialError(404, str(exc)) from exc
        if detail.availability != "available":
            raise TrialError(
                409, f"方案包不可试跑: {detail.unavailable_reason or '不可用'}")
        for scene_out in detail.experience.scenes:
            if scene_out.id == scene_id:
                if not scene_out.trial_available:
                    raise TrialError(
                        409, f"场景不可试跑: {scene_out.degrade_reason or '缺少试跑源'}")
                return
        raise TrialError(404, f"场景不存在: {scene_id}")

    def _build_source(self, pack, scene, kind: SourceKind,
                      video_id: int | None, camera_id: int | None) -> _SourceSpec:
        if kind == "pack":
            from .catalog import safe_resolve_pack_path

            rel = scene.trial_source
            path = (safe_resolve_pack_path(pack.base_dir.resolve(), rel)
                    if rel else None)
            if path is None or not path.is_file():
                raise TrialError(409, "场景不可试跑: 缺少包内试跑源")
            return self._file_source(path, kind)
        if kind == "video":
            if video_id is None:
                raise TrialError(422, "视频库来源必须指定 video_id")
            session = get_session()
            try:
                video = session.get(Video, video_id)
            finally:
                session.close()
            if video is None:
                raise TrialError(422, f"视频不存在: {video_id}")
            path = resolve_source_uri(video.path)
            if not path.is_file():
                raise TrialError(422, f"视频文件不存在: {video.filename}")
            return self._file_source(path, kind)
        if kind == "camera":
            if camera_id is None:
                raise TrialError(422, "摄像头来源必须指定 camera_id")
            session = get_session()
            try:
                camera = session.get(Camera, camera_id)
            finally:
                session.close()
            if camera is None:
                raise TrialError(422, f"摄像头不存在: {camera_id}")
            if not camera_manager.is_running(camera_id):
                raise TrialError(422, "摄像头未在运行，不能作为试跑源")
            frame = _wait_first_frame(camera_id, timeout=3.0)
            if frame is None:
                raise TrialError(503, "体验源不可用: 摄像头暂无画面")
            h, w = frame.shape[:2]
            return _SourceSpec(kind=kind, camera_id=camera_id, width=w, height=h)
        raise TrialError(422, f"不支持的试跑来源: {kind}")

    def _file_source(self, path: Path, kind: SourceKind) -> _SourceSpec:
        try:
            width, height = probe_resolution(str(path))
        except PackError as exc:
            raise TrialError(503, f"体验源不可用: {exc}") from exc
        capture = make_worker("file", str(path))
        capture.start()
        return _SourceSpec(kind=kind, capture=capture, width=width, height=height)

    @staticmethod
    def _build_rules(pack, camera_slot: str, width: int,
                     height: int) -> list[_TrialRule]:
        templates = [t for t in pack.rules
                     if t.camera == camera_slot or t.camera is None]
        if not templates:
            raise TrialError(409, "场景不可试跑: 该机位没有规则")
        rules: list[_TrialRule] = []
        for i, tpl in enumerate(templates):
            params = {k: v for k, v in (tpl.params or {}).items()
                      if k != "active_hours"}  # 试跑不受生效时段限制
            stub = SimpleNamespace(
                id=i + 1,
                type=tpl.type,
                params=scale_params(params, width, height),
                cooldown=tpl.cooldown,
                enabled=True,
            )
            rules.append(_TrialRule(
                stub=stub, stable_id=tpl.id or f"rule-{i + 1}",
                name=tpl.name, type=tpl.type))
        return rules

    @staticmethod
    def _build_detector():
        try:
            return build_detector()
        except Exception as exc:  # noqa: BLE001
            raise TrialError(503, f"检测器不可用: {exc}") from exc


def _find_scene(manifest, scene_id: str):
    if manifest.experience is None:
        return None
    for scene in manifest.experience.scenes:
        if scene.id == scene_id:
            return scene
    return None


def _wait_first_frame(camera_id: int, timeout: float) -> np.ndarray | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = camera_manager.latest_frame(camera_id)
        if frame is not None:
            return frame
        time.sleep(0.1)
    return None


# 全局单例
pack_experience = PackExperience()
