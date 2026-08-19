"""训练任务标注 API：触发 VLM 打标、人工确认队列。

创建任务 / 抽帧由 CAM-3 骨架负责；本模块只消费
data/training/<task_id>/ 下已有的 definition.json 与 frames/。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..training.label import annotate_task, apply_review, pending_review
from ..training.storage import load_definition, task_exists

router = APIRouter(prefix="/training/tasks", tags=["training"])


class ReviewAction(BaseModel):
    action: str = Field(description="confirm 点选类别，skip 跳过")
    label: Optional[str] = Field(None, description="confirm 时必填，须为任务封闭类别")


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
