"""端到端冒烟：合成视频（移动矩形）→ 文件源 → mock detector → 规则 → 事件落库 + 快照落盘。

真实 YOLO 对合成图形可能检不出，这里用 OPENCAM_DETECTOR=mock 验证整条链路。
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from opencam.config import resolve_snapshot_path
from opencam.db import get_session, init_db
from opencam.detection.rules import RuleHit
from opencam.models import CAMERA_RUNNING, Camera, Event, Rule
from opencam.pipeline import persist_hit, start_camera, stop_camera

W, H = 320, 240


def _make_video(path: Path, frames: int = 90, fps: int = 15) -> None:
    """生成一段移动白色矩形的合成视频。"""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (W, H))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        x = int((W - 60) * i / frames)
        cv2.rectangle(frame, (x, 80), (x + 50, 180), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


@pytest.fixture()
def e2e_env(tmp_settings, tmp_path):
    """初始化隔离 DB 并造好一路全屏区域入侵规则的摄像头。"""
    init_db(tmp_settings.db_url)
    video = tmp_path / "synthetic.mp4"
    _make_video(video)

    session = get_session()
    try:
        camera = Camera(name="e2e", source_type="file", source_uri=str(video),
                        status=CAMERA_RUNNING)
        session.add(camera)
        session.commit()
        # 全屏区域，冷却 1 秒，mock detector 的移动框必然触发
        rule = Rule(camera_id=camera.id, type="zone_intrusion",
                    params={"polygon": [[0, 0], [W, 0], [W, H], [0, H]]},
                    cooldown=1.0)
        session.add(rule)
        session.commit()
        return camera.id
    finally:
        session.close()


def test_pipeline_end_to_end(e2e_env):
    camera_id = e2e_env
    start_camera(camera_id)
    try:
        # 等流水线产出事件，最多 15 秒
        deadline = time.time() + 15
        events = []
        while time.time() < deadline:
            session = get_session()
            try:
                events = session.query(Event).filter_by(
                    camera_id=camera_id).all()
            finally:
                session.close()
            if events:
                break
            time.sleep(0.5)
    finally:
        stop_camera(camera_id)

    assert events, "流水线未产生任何事件"
    event = events[0]
    assert event.type == "zone_intrusion"
    assert event.snapshot_path is not None
    # snapshot_path 存相对 data_dir 的路径，用 resolve_snapshot_path 解析
    assert resolve_snapshot_path(event.snapshot_path).exists(), "快照文件未落盘"
    assert event.source_offset is not None, "文件源事件应记录素材播放位置"
    assert event.source_offset >= 0
    assert event.intent == "alert"
    assert event.needs_action is True
    assert event.status == "open"
    # 无 OPENCAM_VLM_API_KEY 时，事件应被标记 skipped 或仍 pending
    assert event.vlm_status in ("skipped", "pending")


def test_persist_hit_observe_is_logged_not_todo(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        camera = Camera(name="门口", source_type="file", source_uri="/tmp/x.mp4",
                        status=CAMERA_RUNNING)
        session.add(camera)
        session.commit()
        rule = Rule(
            camera_id=camera.id, type="line_crossing", intent="observe",
            escalate={}, cooldown=0,
            params={"line": [[0, 120], [320, 120]], "direction": "both"})
        session.add(rule)
        session.commit()
        hit = RuleHit(rule_id=rule.id, rule_type="line_crossing",
                      confidence=0.8, detail={"direction": "in", "count": 1})
        event = persist_hit(session, camera.id, rule, hit, None)
        assert event.intent == "observe"
        assert event.needs_action is False
        assert event.status == "logged"
        assert event.vlm_status == "skipped"
    finally:
        session.close()
