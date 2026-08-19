"""事件 API：列表（过滤/分页）、详情、ack、快照。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Event, EventOut
from ..training.feedback import FeedbackError, ingest_event_feedback

router = APIRouter(prefix="/events", tags=["events"])


class EventFeedback(BaseModel):
    task_id: str = Field(description="归入的训练任务 id")
    kind: str = Field(description="false_alarm 误报 / miss 漏报")
    label: Optional[str] = Field(None, description="覆盖默认类别")


@router.get("", response_model=list[EventOut], summary="事件列表", description="支持 camera_id / rule_type / vlm_verdict / acked 过滤与 limit/offset 分页，按时间倒序。")
def list_events(
    camera_id: Optional[int] = Query(None, description="按摄像头过滤"),
    rule_type: Optional[str] = Query(
        None, description="按规则类型过滤，如 zone_intrusion / line_crossing"),
    vlm_verdict: Optional[str] = Query(
        None, description="按 VLM 判定过滤：confirmed / false_alarm / uncertain"),
    acked: Optional[bool] = Query(None, description="按确认状态过滤"),
    limit: int = Query(50, ge=1, le=500, description="每页条数"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    session: Session = Depends(session_scope),
):
    q = session.query(Event)
    if camera_id is not None:
        q = q.filter(Event.camera_id == camera_id)
    if rule_type is not None:
        q = q.filter(Event.type == rule_type)
    if vlm_verdict is not None:
        q = q.filter(Event.vlm_verdict == vlm_verdict)
    if acked is not None:
        q = q.filter(Event.acked == acked)
    return q.order_by(Event.ts.desc()).offset(offset).limit(limit).all()


@router.get("/{event_id}", response_model=EventOut, summary="事件详情", description="含命中目标、快照路径与 VLM 复核结论。")
def get_event(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    return event


@router.post("/{event_id}/ack", response_model=EventOut, summary="确认事件（ack）")
def ack_event(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    event.acked = True
    session.commit()
    session.refresh(event)
    return event


@router.get("/{event_id}/snapshot", summary="事件快照图（JPEG）")
def event_snapshot(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    if not event.snapshot_path or not Path(event.snapshot_path).exists():
        raise HTTPException(404, "快照不存在")
    return FileResponse(event.snapshot_path, media_type="image/jpeg")


@router.post("/{event_id}/feedback", summary="误报/漏报反馈并写入训练数据集")
def event_feedback(event_id: int, body: EventFeedback,
                   session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    try:
        sample = ingest_event_feedback(
            body.task_id, event.id, body.kind,
            event.snapshot_path, label=body.label)
    except FeedbackError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    event.acked = True
    session.commit()
    return {
        "event_id": event.id,
        "acked": True,
        "task_id": body.task_id,
        "sample": sample,
    }
