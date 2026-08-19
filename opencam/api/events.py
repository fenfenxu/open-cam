"""事件 API：列表（过滤/分页）、详情、处置编辑（PATCH）、处置时间线、ack、快照、素材片段、重发通知。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..clip import clip_file_for_event, media_type_for, resolve_source_uri
from ..config import resolve_snapshot_path
from ..db import session_scope
from ..models import (EVENT_ACKED, EVENT_OPEN, Camera, Event, EventAction,
                      EventActionOut, EventOut, EventUpdate)
from ..notify import notifier
from ..training.feedback import FeedbackError, ingest_event_feedback

router = APIRouter(prefix="/events", tags=["events"])


class EventFeedback(BaseModel):
    task_id: str = Field(description="归入的训练任务 id")
    kind: str = Field(description="false_alarm 误报 / miss 漏报")
    label: Optional[str] = Field(None, description="覆盖默认类别")


def _get_event(session: Session, event_id: int) -> Event:
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    return event


def _event_outs(session: Session, events: list[Event]) -> list[EventOut]:
    """补摄像头名与源文件名，列表/详情才能看出是哪路、哪段素材。"""
    ids = {e.camera_id for e in events}
    cams: dict[int, Camera] = {}
    if ids:
        cams = {c.id: c for c in session.query(Camera).filter(Camera.id.in_(ids)).all()}
    out: list[EventOut] = []
    for event in events:
        payload = EventOut.model_validate(event)
        cam = cams.get(event.camera_id)
        if cam is None:
            out.append(payload)
            continue
        filename = Path(cam.source_uri).name if cam.source_type == "file" else None
        out.append(payload.model_copy(update={
            "camera_name": cam.name,
            "source_filename": filename,
        }))
    return out


def _event_out(session: Session, event: Event) -> EventOut:
    return _event_outs(session, [event])[0]


def _log_action(session: Session, event_id: int, action: str,
                payload: dict, actor: str = "local") -> None:
    session.add(EventAction(event_id=event_id, action=action,
                            actor=actor, payload=payload))


@router.get("", response_model=list[EventOut], summary="事件列表", description="支持 camera_id / rule_type / vlm_verdict / acked / status / starred 过滤与 limit/offset 分页，按时间倒序。")
def list_events(
    camera_id: Optional[int] = Query(None, description="按摄像头过滤"),
    rule_type: Optional[str] = Query(
        None, description="按规则类型过滤，如 zone_intrusion / line_crossing"),
    vlm_verdict: Optional[str] = Query(
        None, description="按 VLM 判定过滤：confirmed / false_alarm / uncertain"),
    acked: Optional[bool] = Query(None, description="按确认状态过滤"),
    status: Optional[str] = Query(
        None, description="按处置状态过滤：open / acked / resolved / ignored"),
    starred: Optional[bool] = Query(None, description="仅看关注（星标）事件"),
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
    if status is not None:
        q = q.filter(Event.status == status)
    if starred is not None:
        q = q.filter(Event.starred == starred)
    events = q.order_by(Event.ts.desc()).offset(offset).limit(limit).all()
    return _event_outs(session, events)


@router.get("/{event_id}", response_model=EventOut, summary="事件详情", description="含命中目标、快照路径、VLM 复核结论与处置状态。")
def get_event(event_id: int, session: Session = Depends(session_scope)):
    return _event_out(session, _get_event(session, event_id))


@router.patch("/{event_id}", response_model=EventOut, summary="编辑处置信息", description="更新状态/星标/负责人/备注；每项实际变更记入处置时间线。status=acked 时同步 acked=true。")
def update_event(event_id: int, body: EventUpdate,
                 session: Session = Depends(session_scope)):
    event = _get_event(session, event_id)
    data = body.model_dump(exclude_unset=True)

    if "status" in data and data["status"] != event.status:
        _log_action(session, event.id, "status",
                    {"from": event.status, "to": data["status"]})
        event.status = data["status"]
        event.acked = event.status != EVENT_OPEN
    if "starred" in data and data["starred"] != event.starred:
        _log_action(session, event.id,
                    "star" if data["starred"] else "unstar", {})
        event.starred = data["starred"]
    if "assignee" in data and data["assignee"] != event.assignee:
        _log_action(session, event.id, "assign",
                    {"from": event.assignee, "to": data["assignee"]})
        event.assignee = data["assignee"]
    if "note" in data and data["note"] != event.note:
        _log_action(session, event.id, "note", {"text": data["note"]})
        event.note = data["note"]

    session.commit()
    session.refresh(event)
    return _event_out(session, event)


@router.get("/{event_id}/actions", response_model=list[EventActionOut],
            summary="处置时间线", description="该事件的全部处置记录（关注/指派/状态/备注/通知），按时间升序。")
def list_actions(event_id: int, session: Session = Depends(session_scope)):
    _get_event(session, event_id)
    return (session.query(EventAction).filter_by(event_id=event_id)
            .order_by(EventAction.ts.asc()).all())


@router.post("/{event_id}/ack", response_model=EventOut, summary="确认事件（ack）")
def ack_event(event_id: int, session: Session = Depends(session_scope)):
    event = _get_event(session, event_id)
    if not event.acked:
        _log_action(session, event.id, "ack", {})
    event.acked = True
    event.status = EVENT_ACKED
    session.commit()
    session.refresh(event)
    return _event_out(session, event)


@router.post("/{event_id}/notify", summary="重发通知", description="把该事件重新推送到所有匹配的通知渠道，返回推送渠道数。")
def resend_notify(event_id: int, session: Session = Depends(session_scope)):
    _get_event(session, event_id)
    notifier.submit(event_id)
    return {"ok": True}


@router.get("/{event_id}/snapshot", summary="事件快照图（JPEG）")
def event_snapshot(event_id: int, session: Session = Depends(session_scope)):
    event = _get_event(session, event_id)
    if not event.snapshot_path or ".." in Path(event.snapshot_path).parts:
        raise HTTPException(404, "快照不存在")
    # 兼容新旧数据：新数据存相对 data_dir 路径，旧数据是绝对路径
    path = resolve_snapshot_path(event.snapshot_path)
    if not path.exists():
        raise HTTPException(404, "快照不存在")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{event_id}/clip", summary="事件对应的素材片段",
            description="文件源事件按命中点前后窗口回放；优先返回抽出的短 mp4，否则回源文件。")
def event_clip(event_id: int, session: Session = Depends(session_scope)):
    event = _get_event(session, event_id)
    if event.source_offset is None:
        raise HTTPException(404, "该事件没有素材播放位置")
    camera = session.get(Camera, event.camera_id)
    if camera is None or camera.source_type != "file":
        raise HTTPException(404, "该事件没有可回放的视频文件")
    source = resolve_source_uri(camera.source_uri)
    if not source.is_file():
        raise HTTPException(404, "源视频文件不存在")
    clip = clip_file_for_event(event.id, source, event.source_offset)
    if clip is None or not clip.is_file():
        raise HTTPException(404, "无法生成可回放片段")
    return FileResponse(clip, media_type=media_type_for(clip), filename=clip.name)


@router.post("/{event_id}/feedback", summary="误报/漏报反馈并写入训练数据集")
def event_feedback(event_id: int, body: EventFeedback,
                   session: Session = Depends(session_scope)):
    event = _get_event(session, event_id)
    if not event.snapshot_path or ".." in Path(event.snapshot_path).parts:
        raise HTTPException(404, "快照不存在")
    snap = resolve_snapshot_path(event.snapshot_path)
    try:
        sample = ingest_event_feedback(
            body.task_id, event.id, body.kind,
            str(snap), label=body.label)
    except FeedbackError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if not event.acked:
        _log_action(session, event.id, "ack", {"source": "feedback"})
    event.acked = True
    event.status = EVENT_ACKED
    session.commit()
    return {
        "event_id": event.id,
        "acked": True,
        "task_id": body.task_id,
        "sample": sample,
    }
