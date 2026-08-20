"""事件通知：异步队列把命中事件推送到员工个人渠道与群机器人 webhook。

- 仿 VLM 复核线程：daemon 线程 + 队列消费，绝不阻塞主链路。
- 只对待办（needs_action=true）：先按 event_routings 匹配员工推个人渠道，
  再按 NotifyChannel 通配匹配群渠道；两者独立，可同时推。
- 多员工命中全部推送，assignee_id 取路由 id 最小的员工，assignee 双写其名字。
- 渠道的 camera_id / rule_type 为空表示通配；推送结果逐渠道记入 EventAction。
- 无匹配目标时直接丢弃，不记日志噪音。
"""

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
    """组织推送 payload：事件摘要 + 处置上下文。只含元数据，不含快照字节。"""
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
        "detail": event.detail,
    }


def match_channels(session, event: Event) -> list[NotifyChannel]:
    """匹配启用中的群渠道：camera_id / rule_type 为空视为通配。"""
    channels = session.query(NotifyChannel).filter_by(enabled=True).all()
    return [
        ch for ch in channels
        if (ch.camera_id is None or ch.camera_id == event.camera_id)
        and (ch.rule_type is None or ch.rule_type == event.type)
    ]


def match_routings(session, event: Event) -> list[tuple[EventRouting, Person]]:
    """匹配启用中的事件路由（空值通配），按路由 id 升序去重返回员工。"""
    routings = (session.query(EventRouting).filter_by(enabled=True)
                .order_by(EventRouting.id.asc()).all())
    matched: list[tuple[EventRouting, Person]] = []
    seen: set[int] = set()
    for routing in routings:
        if routing.person_id in seen:
            continue
        if (routing.camera_id is not None
                and routing.camera_id != event.camera_id):
            continue
        if (routing.rule_type is not None
                and routing.rule_type != event.type):
            continue
        person = session.get(Person, routing.person_id)
        if person is None:
            continue
        seen.add(person.id)
        matched.append((routing, person))
    return matched


def send_webhook(client: httpx.Client, url: str, payload: dict[str, Any]) -> None:
    """POST 一条 webhook，失败抛异常由调用方记录。"""
    resp = client.post(url, json=payload, timeout=_NOTIFY_TIMEOUT)
    resp.raise_for_status()


def notify_event(event_id: int, client: Optional[httpx.Client] = None) -> int:
    """把待办事件推送到匹配的员工个人渠道与群渠道；返回推送的渠道数。

    needs_action 为假的观察记录直接返回 0。同步实现，供 Notifier 线程与
    「重发通知」API 复用。
    """
    session = get_session()
    own_client = client is None
    if own_client:
        client = httpx.Client()
    try:
        event = session.get(Event, event_id)
        if event is None or not event.needs_action:
            return 0
        # (actor, webhook, kind)：先个人渠道，后群渠道
        targets: list[tuple[str, str, Optional[str]]] = []
        routed = match_routings(session, event)
        if routed:
            owner = routed[0][1]
            if event.assignee_id != owner.id:
                session.add(EventAction(
                    event_id=event_id, action="assign", actor="agent",
                    payload={"from": event.assignee_id, "to": owner.id}))
                event.assignee_id = owner.id
                event.assignee = owner.name
                session.commit()
        for _, person in routed:
            channels = (session.query(PersonChannel)
                        .filter_by(person_id=person.id, enabled=True).all())
            for ch in channels:
                targets.append((person.name, ch.webhook, ch.kind))
        for ch in match_channels(session, event):
            targets.append((ch.name, ch.webhook, None))
        if not targets:
            return 0
        payload = event_payload(event)
        for actor, webhook, kind in targets:
            try:
                send_webhook(client, webhook, payload)
                result: dict[str, Any] = {"ok": True, "webhook": webhook}
            except Exception as exc:  # noqa: BLE001 单渠道失败不影响其他渠道
                logger.warning("通知 %s 推送失败 event=%d: %s",
                               actor, event_id, exc)
                result = {"ok": False, "webhook": webhook,
                          "error": str(exc)[:200]}
            if kind is not None:
                result["kind"] = kind
            session.add(EventAction(event_id=event_id, action="notify",
                                    actor=actor, payload=result))
        session.commit()
        return len(targets)
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

    # ---- 内部 ----

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


# 全局单例
notifier = Notifier()
