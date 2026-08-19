"""摄像头管理 API：CRUD + 启停 + 实时抓帧 + 视频上传别名。"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import (
    CAMERA_RUNNING,
    CAMERA_STOPPED,
    BatchIds,
    BatchResult,
    BatchResultItem,
    Camera,
    CameraCreate,
    CameraHealth,
    CameraOut,
    CameraUpdate,
    Event,
    Rule,
    VideoOut,
)
from ..pipeline import start_camera, stop_camera
from ..streams.manager import camera_manager
from .videos import store_upload

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _health_for(camera_id: int, status: str) -> CameraHealth | None:
    if status != CAMERA_RUNNING:
        return None
    worker = camera_manager.get(camera_id)
    if worker is None:
        return None
    frame = worker.latest_frame()
    has_frame = frame is not None
    age = None
    if worker.last_frame_at:  # 0.0 视为从未出帧
        age = time.monotonic() - worker.last_frame_at
    width = height = None
    if frame is not None:
        height, width = int(frame.shape[0]), int(frame.shape[1])
    return CameraHealth(
        alive=worker.is_alive(),
        has_frame=has_frame,
        last_frame_age_sec=age,
        width=width,
        height=height,
    )


def camera_out(camera: Camera) -> CameraOut:
    return CameraOut(
        id=camera.id,
        name=camera.name,
        source_type=camera.source_type,
        source_uri=camera.source_uri,
        status=camera.status,
        health=_health_for(camera.id, camera.status),
    )


def _batch(ids: list[int], start: bool, session: Session) -> BatchResult:
    results: list[BatchResultItem] = []
    for camera_id in ids:
        camera = session.get(Camera, camera_id)
        if camera is None:
            results.append(BatchResultItem(id=camera_id, ok=False, error="摄像头不存在"))
            continue
        try:
            if start:
                if camera.status != CAMERA_RUNNING:
                    start_camera(camera_id)
            else:
                if camera.status != CAMERA_STOPPED:
                    stop_camera(camera_id)
            results.append(BatchResultItem(id=camera_id, ok=True))
        except Exception as exc:  # noqa: BLE001
            results.append(BatchResultItem(id=camera_id, ok=False, error=str(exc)))
    return BatchResult(results=results)


@router.post("/upload", response_model=VideoOut, status_code=201, summary="上传本地视频文件")
def upload_video(file: UploadFile):
    """别名：与 POST /videos 同一套入库，响应含 path 以兼容旧客户端。"""
    return store_upload(file)


@router.get("", response_model=list[CameraOut], summary="摄像头列表")
def list_cameras(session: Session = Depends(session_scope)):
    return [camera_out(c) for c in session.query(Camera).order_by(Camera.id).all()]


@router.post("", response_model=CameraOut, status_code=201, summary="创建摄像头", description="source_type 为 file（视频文件）或 rtsp；autostart=true 时创建即启动采集与分析。")
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
    return camera_out(camera)


@router.post("/batch/start", response_model=BatchResult, summary="批量启动摄像头")
def batch_start(body: BatchIds, session: Session = Depends(session_scope)):
    return _batch(body.ids, start=True, session=session)


@router.post("/batch/stop", response_model=BatchResult, summary="批量停止摄像头")
def batch_stop(body: BatchIds, session: Session = Depends(session_scope)):
    return _batch(body.ids, start=False, session=session)


@router.get("/{camera_id}", response_model=CameraOut, summary="摄像头详情")
def get_camera(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    return camera_out(camera)


@router.put("/{camera_id}", response_model=CameraOut, summary="更新摄像头")
def update_camera(camera_id: int, body: CameraUpdate,
                  session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    source_touched = "source_type" in body.model_fields_set or "source_uri" in body.model_fields_set
    if source_touched and camera.status == CAMERA_RUNNING:
        raise HTTPException(409, "请先停止摄像头再修改视频源")
    if body.name is not None:
        camera.name = body.name
    if body.source_type is not None:
        camera.source_type = body.source_type
    if body.source_uri is not None:
        camera.source_uri = body.source_uri
    session.commit()
    session.refresh(camera)
    return camera_out(camera)


@router.delete("/{camera_id}", status_code=204, summary="删除摄像头", description="运行中的摄像头会先停止再删除。级联删除规则、事件与快照文件，不删除 uploads 视频。")
def delete_camera(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status == CAMERA_RUNNING:
        stop_camera(camera_id)
    events = session.query(Event).filter_by(camera_id=camera_id).all()
    snap_root = settings.snapshot_dir.resolve()
    for event in events:
        if not event.snapshot_path:
            continue
        path = Path(event.snapshot_path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.exists() and (resolved == snap_root or snap_root in resolved.parents):
            resolved.unlink(missing_ok=True)
    session.query(Event).filter_by(camera_id=camera_id).delete()
    session.query(Rule).filter_by(camera_id=camera_id).delete()
    session.delete(camera)
    session.commit()
    return Response(status_code=204)


@router.post("/{camera_id}/reconnect", response_model=CameraOut, summary="重连摄像头")
def reconnect(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status != CAMERA_RUNNING:
        raise HTTPException(409, "仅运行中的摄像头可以重连")
    try:
        stop_camera(camera_id)
        start_camera(camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"重连失败: {exc}") from exc
    session.refresh(camera)
    return camera_out(camera)


@router.post("/{camera_id}/start", response_model=CameraOut, summary="启动摄像头", description="启动采集与分析流水线；幂等。")
def start(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status == CAMERA_RUNNING:
        return camera_out(camera)
    try:
        start_camera(camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"启动失败: {exc}") from exc
    session.refresh(camera)
    return camera_out(camera)


@router.post("/{camera_id}/stop", response_model=CameraOut, summary="停止摄像头")
def stop(camera_id: int, session: Session = Depends(session_scope)):
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if camera.status != CAMERA_STOPPED:
        stop_camera(camera_id)
        session.refresh(camera)
    return camera_out(camera)


@router.get("/{camera_id}/snapshot.jpg", summary="当前实时帧（JPEG）", description="摄像头未运行或无可用帧时返回 503。")
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
