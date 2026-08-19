"""训练标注 API：跑自动打标、人工确认队列、裁剪图。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    SAMPLE_PENDING,
    LabelRunOut,
    ReviewDecision,
    ReviewQueueOut,
    TrainingSample,
    TrainingSampleOut,
    TrainingTask,
)
from ..training.pipeline import apply_human_decision, run_labeling, sample_stats
from ..training.vlm_label import definition_fields

router = APIRouter(prefix="/training", tags=["training"])


def _crop_url(sample_id: int) -> str:
    return f"/training/samples/{sample_id}/crop.jpg"


def _sample_out(sample: TrainingSample) -> TrainingSampleOut:
    return TrainingSampleOut(
        id=sample.id,
        task_id=sample.task_id,
        predicted_label=sample.predicted_label,
        confidence=sample.confidence,
        label=sample.label,
        status=sample.status,
        source=sample.source,
        crop_url=_crop_url(sample.id),
    )


def _get_task(session: Session, task_id: int) -> TrainingTask:
    task = session.get(TrainingTask, task_id)
    if task is None:
        raise HTTPException(404, "训练任务不存在")
    return task


@router.post("/tasks/{task_id}/label", response_model=LabelRunOut,
             summary="跑自动标注",
             description="固定区域裁剪抽帧后，用标注侧 VLM 打属性标签；"
                         "高置信入数据集，低置信进人工确认队列。任务级 vlm_config 可覆盖全局。")
def label_task(task_id: int, session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    try:
        stats = run_labeling(session, task)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(task)
    return LabelRunOut(task_id=task.id, status=task.status, stats=stats)


@router.get("/tasks/{task_id}/review", response_model=ReviewQueueOut,
            summary="人工确认队列",
            description="返回待点选的裁剪小图；用户只需点封闭类别或跳过。")
def review_queue(task_id: int, session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    _, _, classes = definition_fields(task.definition or {})
    pending = (
        session.query(TrainingSample)
        .filter_by(task_id=task_id, status=SAMPLE_PENDING)
        .order_by(TrainingSample.id.asc())
        .all()
    )
    return ReviewQueueOut(
        task_id=task.id,
        classes=classes,
        pending=[_sample_out(s) for s in pending],
        stats=sample_stats(session, task_id),
    )


@router.post("/tasks/{task_id}/review/{sample_id}",
             response_model=TrainingSampleOut,
             summary="确认或跳过一张样本")
def review_sample(task_id: int, sample_id: int, body: ReviewDecision,
                  session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    sample = session.get(TrainingSample, sample_id)
    if sample is None or sample.task_id != task_id:
        raise HTTPException(404, "样本不存在")
    if sample.status != SAMPLE_PENDING:
        raise HTTPException(409, "该样本已处理")
    _, _, classes = definition_fields(task.definition or {})
    try:
        sample = apply_human_decision(
            session, sample, label=body.label, skip=body.skip, classes=classes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _sample_out(sample)


@router.get("/samples/{sample_id}/crop.jpg", summary="样本裁剪图")
def sample_crop(sample_id: int, session: Session = Depends(session_scope)):
    sample = session.get(TrainingSample, sample_id)
    if sample is None:
        raise HTTPException(404, "样本不存在")
    path = Path(sample.crop_path)
    if not path.is_file():
        raise HTTPException(404, "裁剪图不存在")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media)
