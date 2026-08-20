"""事件通知：个人渠道 + 群机器人兜底；仅待办推送。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

import httpx

from .db import get_session
from .models import (RULE_TYPE_NAMES, Event, EventAction, EventRouting,
                      NotifyChannel, Person, PersonChannel)

logger = logging.getLogger(__name__)

_NOTIFY_TIMEOUT = 10.0


def event_payload(event: Event) -> dict[str, Any]:
    """组织推送 payload：事件摘要 + 处置上下文。"""
    return {
        "event_id": event.id,
        "camera_id": event.camera_id,
        "type": event.type,
        "type_name": RULE_TYPE_NAMES.get(event.type, event.type),
        "confidence": event.confidence,
        "ts": event.ts,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.ts)),
        "status": event.status,
        "assignee": event.assignee,
        "assignee_id": event.assignee_id,
        "needs_action": event.needs_action,
        "intent": event.intent,
        "repeat_count": event.repeat_count,
        "verdict": event.verdict,
        "detail": event.detail,
    }


def match_routings(session, event: Event) -> list[EventRouting]:
    """匹配启用路由：camera_id / rule_type 为空视为通配，按 id 升序。"""
    routings = (session.query(EventRouting).filter_by(enabled=True)
                .order_by(EventRouting.id.asc()).all())
    return [
        r for r in routings
        if (r.camera_id is None or r.camera_id == event.camera_id)
        and (r.rule_type is None or r.rule_type == event.type)
    ]


def match_channels(session, event: Event) -> list[NotifyChannel]:
    """匹配启用中的群渠道：camera_id / rule_type 为空视为通配。"""
    channels = session.query(NotifyChannel).filter_by(enabled=True).all()
    return [
        ch for ch in channels
        if (ch.camera_id is None or ch.camera_id == event.camera_id)
        and (ch.rule_type is None or ch.rule_type == event.type)
    ]


def send_webhook(client: httpx.Client, url: str, payload: dict[str, Any]) -> None:
    """POST 一条 webhook，失败抛异常由调用方记录。"""
    resp = client.post(url, json=payload, timeout=_NOTIFY_TIMEOUT)
    resp.raise_for_status()


def _push_one(session, event_id: int, client: httpx.Client, url: str,
              actor: str, payload: dict[str, Any]) -> bool:
    try:
        send_webhook(client, url, payload)
        result: dict[str, Any] = {"ok": True, "webhook": url}
        ok = True
    except Exception as exc:  # noqa: BLE001 单渠道失败不影响其他渠道
        logger.warning("通知渠道 %s 推送失败 event=%d: %s", actor, event_id, exc)
        result = {"ok": False, "webhook": url, "error": str(exc)[:200]}
        ok = False
    session.add(EventAction(event_id=event_id, action="notify",
                            actor=actor, payload=result))
    return ok


def notify_event(event_id: int, client: Optional[httpx.Client] = None) -> int:
    """推送待办：先个人渠道再群渠道；返回推送次数。"""
    session = get_session()
    own_client = client is None
    if own_client:
        client = httpx.Client()
    sent = 0
    try:
        event = session.get(Event, event_id)
        if event is None or not event.needs_action:
            return 0

        routings = match_routings(session, event)
        if routings:
            lead = routings[0]
            person = session.get(Person, lead.person_id)
            if person is not None:
                event.assignee_id = person.id
                event.assignee = person.name

        payload = event_payload(event)
        seen_persons: set[int] = set()
        for routing in routings:
            if routing.person_id in seen_persons:
                continue
            seen_persons.add(routing.person_id)
            person = session.get(Person, routing.person_id)
            if person is None:
                continue
            channels = (session.query(PersonChannel)
                        .filter_by(person_id=person.id, enabled=True)
                        .order_by(PersonChannel.id.asc()).all())
            for ch in channels:
                _push_one(session, event_id, client, ch.webhook,
                          f"{person.name}({ch.kind})", payload)
                sent += 1

        for ch in match_channels(session, event):
            _push_one(session, event_id, client, ch.webhook, ch.name, payload)
            sent += 1

        session.commit()
        return sent
    finally:
        session.close()
        if own_client:
            client.close()


class Notifier:
    """后台线程消费通知队列。"""

    def __init__(self):
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="notifier",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, event_id: int) -> None:
        self._queue.put(event_id)

    def _run(self) -> None:
        with httpx.Client() as client:
            while not self._stop.is_set():
                try:
                    event_id = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    notify_event(event_id, client)
                except Exception:  # noqa: BLE001 兜底，保证线程不死
                    logger.exception("通知事件 %d 出现未处理异常", event_id)
                finally:
                    self._queue.task_done()


notifier = Notifier()
