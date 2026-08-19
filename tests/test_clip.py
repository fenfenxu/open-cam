"""事件素材时段：时间格式化、文件源上报播放位置、快照标注。"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from opencam.clip import (
    CLIP_AFTER,
    CLIP_BEFORE,
    annotate_frame,
    clip_window,
    format_clip_range,
    format_media_time,
    resolve_source_uri,
)
from opencam.streams.capture import FileSource


def test_format_media_time():
    assert format_media_time(0) == "00:00.00"
    assert format_media_time(12.3) == "00:12.30"
    assert format_media_time(75.5) == "01:15.50"


def test_clip_window_pads_and_clamps():
    start, end = clip_window(12.5)
    assert start == pytest.approx(12.5 - CLIP_BEFORE)
    assert end == pytest.approx(12.5 + CLIP_AFTER)
    start0, _ = clip_window(0.5)
    assert start0 == 0.0


def test_format_clip_range():
    assert format_clip_range(None) is None
    text = format_clip_range(12.5)
    assert "00:10.50" in text
    assert "00:15.50" in text


def test_resolve_source_uri_existing_file(tmp_path, tmp_settings):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"not-a-real-video")
    assert resolve_source_uri(str(video)) == video
    missing = resolve_source_uri(str(tmp_path / "nope.mp4"))
    assert not missing.is_file()


def test_annotate_frame_draws_time_bar():
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    out = annotate_frame(frame, 3.25)
    assert out.shape == frame.shape
    # 底栏应被画上浅色文字/背景，不再全黑
    assert int(out[-8:, :, :].max()) > 0
    # 原图不应被原地修改
    assert frame.max() == 0


def _make_video(path: Path, frames: int = 40, fps: int = 10) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (160, 120))
    assert writer.isOpened()
    for i in range(frames):
        frame = np.full((120, 160, 3), i % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_file_source_reports_source_offset(tmp_path):
    video = tmp_path / "clip.mp4"
    _make_video(video)
    worker = FileSource(str(video))
    worker.start()
    try:
        deadline = time.time() + 6
        sample = None
        while time.time() < deadline:
            sample = worker.latest_sample()
            if sample is not None and sample.offset is not None and sample.offset > 0.2:
                break
            time.sleep(0.05)
        assert sample is not None, "文件源未产出帧"
        assert sample.offset is not None
        assert 0.0 <= sample.offset <= 5.0
        assert sample.frame.shape[0] == 120
    finally:
        worker.stop()
