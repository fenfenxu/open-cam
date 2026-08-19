"""事件素材时段与文件源解析：播放位置、快照标注、回放窗口、MIME。

文件循环播放时，记录的是「素材内秒数」而不是墙上时钟，便于回看对应片段。
摄像头文件回放 API 也走这里的路径解析与 media_type。
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import settings

logger = logging.getLogger(__name__)

# 回放窗口：命中点前 2 秒、后 3 秒
CLIP_BEFORE = 2.0
CLIP_AFTER = 3.0

_BROWSER_VIDEO = {".mp4", ".webm", ".mov", ".m4v"}
_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
}


def clip_window(offset: float) -> tuple[float, float]:
    """由命中点算出回放起止（秒），起点不小于 0。"""
    start = max(0.0, float(offset) - CLIP_BEFORE)
    end = float(offset) + CLIP_AFTER
    return start, end


def format_media_time(seconds: float) -> str:
    """秒 → mm:ss.ss，供快照叠加与 API/UI 共用。"""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def format_clip_range(offset: Optional[float]) -> Optional[str]:
    if offset is None:
        return None
    start, end = clip_window(offset)
    return f"{format_media_time(start)} – {format_media_time(end)}"


def resolve_source_uri(uri: str) -> Path:
    """把摄像头 source_uri 解析成磁盘路径（绝对路径、相对 cwd、或 data_dir）。"""
    p = Path(uri).expanduser()
    if p.is_file():
        return p.resolve()
    under_data = settings.data_dir / uri
    if under_data.is_file():
        return under_data.resolve()
    by_name = settings.data_dir / p.name
    if by_name.is_file():
        return by_name.resolve()
    return p


def annotate_frame(frame: np.ndarray, offset: Optional[float]) -> np.ndarray:
    """在画面底部叠素材时段（ASCII，OpenCV 默认字体不支持中文）。"""
    out = frame.copy()
    if offset is None:
        return out
    h, w = out.shape[:2]
    bar_h = min(36, max(20, h // 10))
    cv2.rectangle(out, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    label = format_clip_range(offset) or format_media_time(offset)
    scale = 0.45 if w < 400 else 0.6
    cv2.putText(
        out, label, (8, h - max(8, bar_h // 3)),
        cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA,
    )
    return out


def media_type_for(path: Path) -> str:
    """给 FileResponse 的 media_type；未知扩展名回退 octet-stream。"""
    ext = path.suffix.lower()
    if ext in _VIDEO_TYPES:
        return _VIDEO_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def is_browser_playable(path: Path) -> bool:
    return path.suffix.lower() in _BROWSER_VIDEO


def extract_clip(source: Path, start: float, end: float, dest: Path) -> bool:
    """用 ffmpeg 抽出短片到 dest（H.264 mp4）。失败返回 False。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    if dest.is_file() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    duration = max(0.5, end - start)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        tmp.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        logger.debug("ffmpeg 抽片失败: %s", proc.stderr[-200:] if proc.stderr else "")
        return False
    tmp.replace(dest)
    return True


def clip_file_for_event(event_id: int, source: Path, offset: float) -> Optional[Path]:
    """优先返回抽出的短 mp4；抽不出则在浏览器可播时退回原片。"""
    start, end = clip_window(offset)
    dest = settings.data_dir / "clips" / f"event_{event_id}.mp4"
    if extract_clip(source, start, end, dest):
        return dest
    if source.is_file():
        return source
    return None
