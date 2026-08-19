"""视频库 API：上传文件落盘入库、列表/详情/删除。被摄像头 source_uri 引用时不可删。"""

from __future__ import annotations

import re
import time
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session, session_scope
from ..models import Camera, Video, VideoOut

router = APIRouter(prefix="/videos", tags=["videos"])

ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


def _safe_dest(filename: str | None) -> Path:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
    if ext not in ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"不支持的视频格式 {ext or '(无扩展名)'}，"
                                 f"支持: {', '.join(sorted(ALLOWED_VIDEO_EXTS))}")
    safe_name = re.sub(r"[^\w.()-]+", "_", filename or "video" + ext)
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / safe_name
    n = 1
    while dest.exists():
        dest = upload_dir / f"{dest.stem}_{n}{dest.suffix}"
        n += 1
    return dest


def _probe(path: Path) -> tuple[float | None, int | None, int | None]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, None, None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (frames / fps) if fps > 0 and frames > 0 else None
        return duration, (width or None), (height or None)
    finally:
        cap.release()


def store_upload(file: UploadFile) -> Video:
    dest = _safe_dest(file.filename)
    dest.write_bytes(file.file.read())
    duration, width, height = _probe(dest)
    session = get_session()
    try:
        video = Video(
            filename=dest.name,
            path=str(dest),
            size_bytes=dest.stat().st_size,
            duration_sec=duration,
            width=width,
            height=height,
            created_at=time.time(),
        )
        session.add(video)
        session.commit()
        session.refresh(video)
        return video
    finally:
        session.close()


@router.post("", response_model=VideoOut, status_code=201, summary="上传视频文件")
def upload_video(file: UploadFile):
    return store_upload(file)


@router.get("", response_model=list[VideoOut], summary="已上传视频列表")
def list_videos(session: Session = Depends(session_scope)):
    return session.query(Video).order_by(Video.id).all()


@router.get("/{video_id}", response_model=VideoOut, summary="视频详情")
def get_video(video_id: int, session: Session = Depends(session_scope)):
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    return video


@router.delete("/{video_id}", status_code=204, summary="删除已上传视频")
def delete_video(video_id: int, session: Session = Depends(session_scope)):
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    used = session.query(Camera).filter_by(source_uri=video.path).first()
    if used is not None:
        raise HTTPException(409, "视频正被摄像头使用，无法删除")
    path = Path(video.path)
    if path.exists():
        path.unlink()
    session.delete(video)
    session.commit()
    return Response(status_code=204)
