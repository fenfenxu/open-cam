"""训练任务 API：语义目标解构、抽帧、VLM 打标与人工确认队列。

产物落在 data/training/<task_id>/（definition.json + frames/），不进 git。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_session
from ..models import Camera, Video
from ..training.define import (
    confirm_definition,
    decompose_goal,
    explain_metrics,
    load_draft,
    new_task_id,
    normalize_definition,
    save_draft,
)
from ..training.frames import extract_frames
from ..training.label import annotate_task, apply_review, pending_review
from ..training.storage import ensure_task_id, load_definition, task_exists
from ..training.train import training_manager, validate_trainable

router = APIRouter(prefix="/training/tasks", tags=["training"])


class CreateTask(BaseModel):
    goal: str = Field(min_length=1, description="自然语言语义目标")
    confirm: bool = Field(False, description="true 时立刻落 definition.json")
    task_id: Optional[str] = Field(None, description="可选；不传则自动生成")
    definition: Optional[dict[str, Any]] = Field(
        None, description="用户改过的结构化定义；与 confirm 一起提交时优先使用")


class ConfirmTask(BaseModel):
    definition: Optional[dict[str, Any]] = Field(
        None, description="确认时的结构化定义；缺省则用上次草稿")


class ExtractFrames(BaseModel):
    camera_id: Optional[int] = Field(None, description="从摄像头 source_uri 抽帧")
    video_id: Optional[int] = Field(None, description="从上传视频库抽帧")
    source_uri: Optional[str] = Field(None, description="本机视频文件路径")
    max_frames: int = Field(120, ge=1, le=2000)
    hamming_threshold: int = Field(8, ge=0, le=64)


class ReviewAction(BaseModel):
    action: str = Field(description="confirm 点选类别，skip 跳过")
    label: Optional[str] = Field(None, description="confirm 时必填，须为任务封闭类别")


class TrainRequest(BaseModel):
    epochs: int = Field(20, ge=1, le=200, description="微调轮数")
    imgsz: int = Field(224, ge=32, le=1280, description="训练输入边长")
    val_ratio: float = Field(0.2, gt=0, lt=0.5,
                             description="自动标注样本进评估集的比例（确认样本必进评估集）")


class ReviewItem(BaseModel):
    id: str
    suggested_label: Optional[str]
    confidence: float
    reason: str = ""
    classes: list[str]
    crop: str


class ReviewQueue(BaseModel):
    remaining: int
    items: list[ReviewItem]


def _require_task(task_id: str) -> None:
    try:
        if not task_exists(task_id):
            raise HTTPException(404, "训练任务不存在")
    except ValueError:
        raise HTTPException(400, "非法任务 id") from None


def _resolve_source(body: ExtractFrames) -> Path:
    provided = [x is not None for x in
                (body.camera_id, body.video_id, body.source_uri)]
    if sum(provided) != 1:
        raise HTTPException(400, "请只提供 camera_id、video_id、source_uri 之一")
    if body.source_uri:
        return Path(body.source_uri)
    session = get_session()
    try:
        if body.camera_id is not None:
            camera = session.get(Camera, body.camera_id)
            if camera is None:
                raise HTTPException(404, "摄像头不存在")
            return Path(camera.source_uri)
        video = session.get(Video, body.video_id)
        if video is None:
            raise HTTPException(404, "视频不存在")
        return Path(video.path)
    finally:
        session.close()


@router.post("", summary="创建训练任务（语义目标 → 结构化定义）",
             description="默认只返回草稿；confirm=true 才写入 definition.json。")
def create_task(body: CreateTask):
    try:
        task_id = ensure_task_id(body.task_id) if body.task_id else new_task_id()
    except ValueError:
        raise HTTPException(400, "非法任务 id") from None
    if body.definition is not None:
        definition, source = normalize_definition(body.definition), "user"
    else:
        definition, source = decompose_goal(body.goal)
    metrics_explained = explain_metrics(definition.get("metrics") or {})
    payload = {
        "task_id": task_id,
        "goal": body.goal,
        "definition": definition,
        "metrics_explained": metrics_explained,
        "source": source,
    }
    if body.confirm:
        saved = confirm_definition(task_id, definition, goal=body.goal)
        return {**payload, "status": "confirmed", "definition": saved}
    save_draft(task_id, payload)
    return {**payload, "status": "draft"}


@router.post("/{task_id}/confirm", summary="确认任务定义并落库")
def confirm_task(task_id: str, body: ConfirmTask = ConfirmTask()):
    try:
        ensure_task_id(task_id)
    except ValueError:
        raise HTTPException(400, "非法任务 id") from None
    definition = body.definition
    goal = None
    if definition is None:
        try:
            draft = load_draft(task_id)
        except FileNotFoundError:
            raise HTTPException(404, "没有可确认的任务草稿") from None
        definition = draft.get("definition") or {}
        goal = draft.get("goal")
    saved = confirm_definition(task_id, definition, goal=goal)
    return {
        "task_id": task_id,
        "status": "confirmed",
        "definition": saved,
        "metrics_explained": explain_metrics(saved.get("metrics") or {}),
    }


@router.post("/{task_id}/frames", summary="从摄像头录像或上传视频抽帧",
             description="按感知哈希去重后写入 data/training/<task_id>/frames/。")
def extract_task_frames(task_id: str, body: ExtractFrames):
    _require_task(task_id)
    source = _resolve_source(body)
    try:
        return extract_frames(
            task_id, source,
            max_frames=body.max_frames,
            hamming_threshold=body.hamming_threshold,
        )
    except FileNotFoundError:
        raise HTTPException(404, "视频文件不存在") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/{task_id}/annotate", summary="对抽帧做 VLM 自动标注")
def annotate(task_id: str):
    """固定区域裁剪后调用 VLM 打属性标签；高置信入数据集，低置信进确认队列。"""
    _require_task(task_id)
    try:
        return annotate_task(task_id)
    except FileNotFoundError:
        raise HTTPException(404, "训练任务不存在") from None


@router.get("/{task_id}/review", response_model=ReviewQueue,
            summary="人工确认队列",
            description="低置信裁剪图，用户只需点类别或跳过。")
def review_queue(task_id: str):
    _require_task(task_id)
    definition = load_definition(task_id)
    classes = list(definition.get("classes") or [])
    items = []
    for sample in pending_review(task_id):
        items.append(ReviewItem(
            id=sample["id"],
            suggested_label=sample.get("label"),
            confidence=float(sample.get("confidence") or 0.0),
            reason=str(sample.get("reason") or ""),
            classes=classes,
            crop=str(sample.get("crop") or ""),
        ))
    return ReviewQueue(remaining=len(items), items=items)


@router.post("/{task_id}/review/{sample_id}", summary="确认或跳过一条标注")
def review_one(task_id: str, sample_id: str, body: ReviewAction):
    _require_task(task_id)
    try:
        return apply_review(task_id, sample_id, body.action, body.label)
    except KeyError:
        raise HTTPException(404, "样本不存在") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/{task_id}/train", status_code=202,
             summary="启动本地微调训练（后台执行）",
             description="从预训练 YOLO 分类模型微调，产出 best.pt 与评估报告；"
                         "评估集必含人工确认样本。状态用 GET 同路径查询。")
def start_train(task_id: str, body: TrainRequest = TrainRequest()):
    _require_task(task_id)
    try:
        validate_trainable(task_id)
    except FileNotFoundError:
        raise HTTPException(404, "训练任务不存在") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        return training_manager.start(task_id, body.model_dump())
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/{task_id}/train", summary="查询训练状态与评估报告",
            description="训练完成后 result 即人话报告（含三指标与典型对错样本）。")
def train_status(task_id: str):
    _require_task(task_id)
    return training_manager.status(task_id)
