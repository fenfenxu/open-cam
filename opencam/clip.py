"""源文件路径解析与 MIME 推断（供文件回放 FileResponse 使用）。"""

from __future__ import annotations

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


def resolve_source_uri(uri: str) -> Path:
    """把摄像头 source_uri 解析成本机路径；不接受查询参数覆盖。"""
    return Path(uri).expanduser()


def media_type_for(path: Path) -> str:
    return _VIDEO_TYPES.get(path.suffix.lower(), "application/octet-stream")
