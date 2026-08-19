"""通知渠道 API：webhook 渠道的增删改查与测试推送。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (NotifyChannel, NotifyChannelIn, NotifyChannelOut,
                      NotifyChannelUpdate)
from ..notify import send_webhook

router = APIRouter(prefix="/api/notify-channels", tags=["notify"])


def _get_channel(session: Session, channel_id: int) -> NotifyChannel:
    channel = session.get(NotifyChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "通知渠道不存在")
    return channel


@router.get("", response_model=list[NotifyChannelOut], summary="通知渠道列表")
def list_channels(session: Session = Depends(session_scope)):
    return session.query(NotifyChannel).order_by(NotifyChannel.id.asc()).all()


@router.post("", response_model=NotifyChannelOut, status_code=201,
             summary="新建通知渠道", description="camera_id / rule_type 为空表示通配（全部摄像头 / 全部规则类型）。")
def create_channel(body: NotifyChannelIn,
                   session: Session = Depends(session_scope)):
    channel = NotifyChannel(**body.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


@router.patch("/{channel_id}", response_model=NotifyChannelOut,
              summary="更新通知渠道")
def update_channel(channel_id: int, body: NotifyChannelUpdate,
                   session: Session = Depends(session_scope)):
    channel = _get_channel(session, channel_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, key, value)
    session.commit()
    session.refresh(channel)
    return channel


@router.delete("/{channel_id}", status_code=204, summary="删除通知渠道")
def delete_channel(channel_id: int, session: Session = Depends(session_scope)):
    channel = _get_channel(session, channel_id)
    session.delete(channel)
    session.commit()


@router.post("/{channel_id}/test", summary="测试推送", description="立即向该渠道发一条测试消息，返回成功/失败与错误信息。")
def test_channel(channel_id: int, session: Session = Depends(session_scope)):
    channel = _get_channel(session, channel_id)
    payload = {"text": f"open-cam 通知渠道「{channel.name}」测试消息",
               "test": True}
    try:
        with httpx.Client() as client:
            send_webhook(client, channel.webhook, payload)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 把错误透传给调用方
        return {"ok": False, "error": str(exc)[:200]}
