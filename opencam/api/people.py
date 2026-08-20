"""员工、个人渠道与事件路由 API。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (Event, EventRouting, EventRoutingIn, EventRoutingOut,
                      EventRoutingUpdate, Person, PersonChannel, PersonChannelIn,
                      PersonChannelOut, PersonChannelUpdate, PersonCreate,
                      PersonOut, PersonUpdate)
from ..notify import send_webhook

router = APIRouter(tags=["people"])


def _get_person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "员工不存在")
    return person


def _get_routing(session: Session, routing_id: int) -> EventRouting:
    routing = session.get(EventRouting, routing_id)
    if routing is None:
        raise HTTPException(404, "路由不存在")
    return routing


def _get_channel(session: Session, person_id: int, channel_id: int) -> PersonChannel:
    channel = session.get(PersonChannel, channel_id)
    if channel is None or channel.person_id != person_id:
        raise HTTPException(404, "个人渠道不存在")
    return channel


@router.get("/api/people", response_model=list[PersonOut], summary="员工列表")
def list_people(session: Session = Depends(session_scope)):
    return session.query(Person).order_by(Person.id.asc()).all()


@router.post("/api/people", response_model=PersonOut, status_code=201,
             summary="新建员工")
def create_person(body: PersonCreate, session: Session = Depends(session_scope)):
    if body.login_name:
        existing = session.query(Person).filter_by(login_name=body.login_name).first()
        if existing is not None:
            raise HTTPException(400, "登录名已被占用")
    person = Person(name=body.name, login_name=body.login_name)
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


@router.get("/api/people/{person_id}", response_model=PersonOut,
            summary="员工详情")
def get_person(person_id: int, session: Session = Depends(session_scope)):
    return _get_person(session, person_id)


@router.patch("/api/people/{person_id}", response_model=PersonOut,
              summary="更新员工")
def update_person(person_id: int, body: PersonUpdate,
                  session: Session = Depends(session_scope)):
    person = _get_person(session, person_id)
    data = body.model_dump(exclude_unset=True)
    if "login_name" in data and data["login_name"]:
        dup = (session.query(Person)
               .filter(Person.login_name == data["login_name"],
                       Person.id != person_id)
               .first())
        if dup is not None:
            raise HTTPException(400, "登录名已被占用")
    for key, value in data.items():
        setattr(person, key, value)
    if "name" in data:
        session.query(Event).filter_by(assignee_id=person.id).update(
            {"assignee": person.name}, synchronize_session=False)
    session.commit()
    session.refresh(person)
    return person


@router.delete("/api/people/{person_id}", status_code=204, summary="删除员工")
def delete_person(person_id: int, session: Session = Depends(session_scope)):
    person = _get_person(session, person_id)
    session.query(Event).filter_by(assignee_id=person.id).update(
        {"assignee_id": None}, synchronize_session=False)
    session.delete(person)
    session.commit()


@router.get("/api/people/{person_id}/channels",
            response_model=list[PersonChannelOut], summary="个人渠道列表")
def list_channels(person_id: int, session: Session = Depends(session_scope)):
    _get_person(session, person_id)
    return (session.query(PersonChannel).filter_by(person_id=person_id)
            .order_by(PersonChannel.id.asc()).all())


@router.post("/api/people/{person_id}/channels",
             response_model=PersonChannelOut, status_code=201,
             summary="新建个人渠道")
def create_channel(person_id: int, body: PersonChannelIn,
                   session: Session = Depends(session_scope)):
    _get_person(session, person_id)
    channel = PersonChannel(person_id=person_id, **body.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


@router.patch("/api/people/{person_id}/channels/{channel_id}",
              response_model=PersonChannelOut, summary="更新个人渠道")
def update_channel(person_id: int, channel_id: int, body: PersonChannelUpdate,
                   session: Session = Depends(session_scope)):
    channel = _get_channel(session, person_id, channel_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, key, value)
    session.commit()
    session.refresh(channel)
    return channel


@router.delete("/api/people/{person_id}/channels/{channel_id}",
               status_code=204, summary="删除个人渠道")
def delete_channel(person_id: int, channel_id: int,
                   session: Session = Depends(session_scope)):
    channel = _get_channel(session, person_id, channel_id)
    session.delete(channel)
    session.commit()


@router.post("/api/people/{person_id}/channels/{channel_id}/test",
             summary="测试个人渠道推送")
def test_channel(person_id: int, channel_id: int,
                 session: Session = Depends(session_scope)):
    channel = _get_channel(session, person_id, channel_id)
    person = _get_person(session, person_id)
    payload = {"text": f"open-cam 员工「{person.name}」{channel.kind} 渠道测试",
               "test": True}
    try:
        with httpx.Client() as client:
            send_webhook(client, channel.webhook, payload)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


@router.get("/api/event-routings", response_model=list[EventRoutingOut],
            summary="事件路由列表")
def list_routings(session: Session = Depends(session_scope)):
    return session.query(EventRouting).order_by(EventRouting.id.asc()).all()


@router.post("/api/event-routings", response_model=EventRoutingOut,
             status_code=201, summary="新建事件路由")
def create_routing(body: EventRoutingIn, session: Session = Depends(session_scope)):
    if session.get(Person, body.person_id) is None:
        raise HTTPException(400, "员工不存在")
    routing = EventRouting(**body.model_dump())
    session.add(routing)
    session.commit()
    session.refresh(routing)
    return routing


@router.patch("/api/event-routings/{routing_id}", response_model=EventRoutingOut,
              summary="更新事件路由")
def update_routing(routing_id: int, body: EventRoutingUpdate,
                   session: Session = Depends(session_scope)):
    routing = _get_routing(session, routing_id)
    data = body.model_dump(exclude_unset=True)
    if "person_id" in data and session.get(Person, data["person_id"]) is None:
        raise HTTPException(400, "员工不存在")
    for key, value in data.items():
        setattr(routing, key, value)
    session.commit()
    session.refresh(routing)
    return routing


@router.delete("/api/event-routings/{routing_id}", status_code=204,
               summary="删除事件路由")
def delete_routing(routing_id: int, session: Session = Depends(session_scope)):
    routing = _get_routing(session, routing_id)
    session.delete(routing)
    session.commit()
