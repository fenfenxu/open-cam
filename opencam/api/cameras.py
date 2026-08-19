"""摄像头管理 API：CRUD + 启停 + 实时抓帧 + 视频文件上传。"""

from __future__ import annotations

import re

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import CAMERA_RUNNING, CAMERA_STOPPED, Camera, CameraCreate, CameraOut
from ..pipeline import start_camera, stop_camera
from ..streams.manager import camera_manager

router = APIRouter(prefix="/cameras", tags=["cameras"])

# 允许上传的视频格式（OpenCV/ffmpeg 可解码的容器）
ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


@router.post("/upload", status_code=201)
def upload_video(file: UploadFile):
    """上传本地视频文件，保存到数据目录，返回可用作 source_uri 的路径。"""
    ext = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in (file.filename or "") else ""
    if ext not in ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"不支持的视频格式 {ext or '(无扩展名)'}，"
                                 f"支持: {', '.join(sorted(ALLOWED_VIDEO_EXTS))}")
    # 文件名只保留安全字符，避免路径穿越与特殊字符
    safe_name = re.sub(r"[^\w.()-]+", "_", file.filename or "video" + ext)
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / safe_name
    # 重名时追加序号，避免覆盖已有文件
    n = 1
    while dest.exists():
        dest = upload_dir / f"{dest.stem}_{n}{dest.suffix}"
        n += 1
    dest.write_bytes(file.file.read())
    return {"path": str(dest)}


@router.get("", response_model=list[CameraOut])
def list_cameras(session: Session = Depends(session_scope)):
    return session.query(Camera).order_by(Camera.id).all()


@router.post("", response_model=CameraOut, status_code=201)
def create_camera(body: CameraCreate, session: Session = Depends(session_scope)):
    camera = Camera(name=body.name, source_type=body.source_type,
                    source_uri=body.source_uri)
    session.add(camera)
    session.commit()
    session.refresh(camera)
    if body.autostart:
        try:
            start_camera(camera.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"摄像头已创建但启动失败: {exc}") from exc
        session.refresh(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    return camera


@router.delete("/{camera_id}", status_code=204)
def delete_camera(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status == CAMERA_RUNNING:
        stop_camera(camera_id)
    session.delete(camera)
    session.commit()
    return Response(status_code=204)


@router.post("/{camera_id}/start", response_model=CameraOut)
def start(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status == CAMERA_RUNNING:
        return camera
    try:
        start_camera(camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"启动失败: {exc}") from exc
    session.refresh(camera)
    return camera


@router.post("/{camera_id}/stop", response_model=CameraOut)
def stop(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status != CAMERA_STOPPED:
        stop_camera(camera_id)
        session.refresh(camera)
    return camera


@router.get("/{camera_id}/snapshot.jpg")
def snapshot(camera_id: int, session: Session = Depends(session_scope)):
    """返回当前实时帧 JPEG。"""
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    frame = camera_manager.latest_frame(camera_id)
    if frame is None:
        raise HTTPException(503, "暂无可用帧（摄像头未运行或流未就绪）")
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise HTTPException(500, "帧编码失败")
    return Response(content=buf.tobytes(), media_type="image/jpeg")
