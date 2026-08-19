"""源文件路径解析与媒体类型（供文件回放 FileResponse）。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
}


def resolve_source_uri(source_uri: str) -> Path:
    """把摄像头 source_uri 解析为本地路径；不接受查询参数覆盖。"""
    return Path(source_uri).expanduser()


def media_type_for(path: Path) -> str:
    """给 FileResponse 的 media_type；未知扩展名回退 octet-stream。"""
    ext = path.suffix.lower()
    if ext in _VIDEO_TYPES:
        return _VIDEO_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"
