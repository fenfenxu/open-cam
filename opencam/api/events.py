"""事件 API：列表（过滤/分页）、详情、ack、快照。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Event, EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(
    camera_id: Optional[int] = None,
    rule_type: Optional[str] = None,
    vlm_verdict: Optional[str] = None,
    acked: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
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


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    return event


@router.post("/{event_id}/ack", response_model=EventOut)
def ack_event(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    event.acked = True
    session.commit()
    session.refresh(event)
    return event


@router.get("/{event_id}/snapshot")
def event_snapshot(event_id: int, session: Session = Depends(session_scope)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    if not event.snapshot_path or not Path(event.snapshot_path).exists():
        raise HTTPException(404, "快照不存在")
    return FileResponse(event.snapshot_path, media_type="image/jpeg")
