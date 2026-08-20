"""员工 / 个人 IM 渠道 / 事件路由 API。

- 员工可无 login_name（本期不验证登录），路由命中即可当负责人、收 IM。
- 删除员工：渠道与路由级联删；事件 assignee_id 置空，保留 assignee 名字副本。
- 群机器人渠道仍在 api/notify.py，不绑人。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (Event, EventRouting, EventRoutingIn, EventRoutingOut,
                      EventRoutingUpdate, Person, PersonChannel,
                      PersonChannelIn, PersonChannelOut, PersonChannelUpdate,
                      PersonIn, PersonOut, PersonUpdate)
from ..notify import send_webhook

router = APIRouter(prefix="/api/people", tags=["people"])
routing_router = APIRouter(prefix="/api/event-routings", tags=["people"])


def _get_person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "员工不存在")
    return person


def _get_channel(session: Session, person_id: int,
                 channel_id: int) -> PersonChannel:
    channel = session.get(PersonChannel, channel_id)
    if channel is None or channel.person_id != person_id:
        raise HTTPException(404, "员工渠道不存在")
    return channel


def _get_routing(session: Session, routing_id: int) -> EventRouting:
    routing = session.get(EventRouting, routing_id)
    if routing is None:
        raise HTTPException(404, "事件路由不存在")
    return routing


def _commit_unique(session: Session) -> None:
    """login_name 唯一约束冲突转成 400。"""
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(400, "登录名已存在") from None


@router.get("", response_model=list[PersonOut], summary="员工列表")
def list_people(session: Session = Depends(session_scope)):
    return session.query(Person).order_by(Person.id.asc()).all()


@router.post("", response_model=PersonOut, status_code=201, summary="新建员工")
def create_person(body: PersonIn, session: Session = Depends(session_scope)):
    person = Person(**body.model_dump())
    session.add(person)
    _commit_unique(session)
    session.refresh(person)
    return person


@router.get("/{person_id}", response_model=PersonOut, summary="员工详情")
def get_person(person_id: int, session: Session = Depends(session_scope)):
    return _get_person(session, person_id)


@router.patch("/{person_id}", response_model=PersonOut, summary="更新员工")
def update_person(person_id: int, body: PersonUpdate,
                  session: Session = Depends(session_scope)):
    person = _get_person(session, person_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(person, key, value)
    _commit_unique(session)
    session.refresh(person)
    return person


@router.delete("/{person_id}", status_code=204, summary="删除员工",
               description="渠道与路由级联删除；事件 assignee_id 置空，保留 assignee 名字。")
def delete_person(person_id: int, session: Session = Depends(session_scope)):
    person = _get_person(session, person_id)
    for event in session.query(Event).filter_by(assignee_id=person_id).all():
        event.assignee_id = None
    session.query(EventRouting).filter_by(person_id=person_id).delete()
    session.delete(person)  # channels 走 ORM 级联
    session.commit()


# ---- 个人 IM 渠道 ----

@router.get("/{person_id}/channels", response_model=list[PersonChannelOut],
            summary="员工渠道列表")
def list_channels(person_id: int, session: Session = Depends(session_scope)):
    _get_person(session, person_id)
    return (session.query(PersonChannel).filter_by(person_id=person_id)
            .order_by(PersonChannel.id.asc()).all())


@router.post("/{person_id}/channels", response_model=PersonChannelOut,
             status_code=201, summary="新建员工渠道")
def create_channel(person_id: int, body: PersonChannelIn,
                   session: Session = Depends(session_scope)):
    _get_person(session, person_id)
    channel = PersonChannel(person_id=person_id, **body.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


@router.patch("/{person_id}/channels/{channel_id}",
              response_model=PersonChannelOut, summary="更新员工渠道")
def update_channel(person_id: int, channel_id: int, body: PersonChannelUpdate,
                   session: Session = Depends(session_scope)):
    channel = _get_channel(session, person_id, channel_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, key, value)
    session.commit()
    session.refresh(channel)
    return channel


@router.delete("/{person_id}/channels/{channel_id}", status_code=204,
               summary="删除员工渠道")
def delete_channel(person_id: int, channel_id: int,
                   session: Session = Depends(session_scope)):
    channel = _get_channel(session, person_id, channel_id)
    session.delete(channel)
    session.commit()


@router.post("/{person_id}/channels/{channel_id}/test", summary="测试推送",
             description="立即向该员工渠道发一条测试消息，返回成功/失败与错误信息。")
def test_channel(person_id: int, channel_id: int,
                 session: Session = Depends(session_scope)):
    person = _get_person(session, person_id)
    channel = _get_channel(session, person_id, channel_id)
    payload = {"text": f"open-cam 员工「{person.name}」渠道测试消息",
               "test": True}
    try:
        with httpx.Client() as client:
            send_webhook(client, channel.webhook, payload)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 把错误透传给调用方
        return {"ok": False, "error": str(exc)[:200]}


# ---- 事件路由（摄像头 × 规则类型 → 员工，空值通配） ----

@routing_router.get("", response_model=list[EventRoutingOut],
                    summary="事件路由列表")
def list_routings(session: Session = Depends(session_scope)):
    return session.query(EventRouting).order_by(EventRouting.id.asc()).all()


@routing_router.post("", response_model=EventRoutingOut, status_code=201,
                     summary="新建事件路由")
def create_routing(body: EventRoutingIn,
                   session: Session = Depends(session_scope)):
    if session.get(Person, body.person_id) is None:
        raise HTTPException(400, "员工不存在")
    routing = EventRouting(**body.model_dump())
    session.add(routing)
    session.commit()
    session.refresh(routing)
    return routing


@routing_router.patch("/{routing_id}", response_model=EventRoutingOut,
                      summary="更新事件路由")
def update_routing(routing_id: int, body: EventRoutingUpdate,
                   session: Session = Depends(session_scope)):
    routing = _get_routing(session, routing_id)
    data = body.model_dump(exclude_unset=True)
    if (data.get("person_id") is not None
            and session.get(Person, data["person_id"]) is None):
        raise HTTPException(400, "员工不存在")
    for key, value in data.items():
        setattr(routing, key, value)
    session.commit()
    session.refresh(routing)
    return routing


@routing_router.delete("/{routing_id}", status_code=204, summary="删除事件路由")
def delete_routing(routing_id: int, session: Session = Depends(session_scope)):
    routing = _get_routing(session, routing_id)
    session.delete(routing)
    session.commit()
