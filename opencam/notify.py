"""事件通知：异步队列把命中事件推送到配置的 webhook 渠道（飞书/企业微信/钉钉机器人等）。

- 仿 VLM 复核线程：daemon 线程 + 队列消费，绝不阻塞主链路。
- 渠道的 camera_id / rule_type 为空表示通配；推送结果逐渠道记入 EventAction。
- 无启用渠道时直接丢弃，不记日志噪音。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

import httpx

from .db import get_session
from .models import RULE_TYPE_NAMES, Event, EventAction, NotifyChannel

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
        "detail": event.detail,
    }


def match_channels(session, event: Event) -> list[NotifyChannel]:
    """匹配启用中的渠道：camera_id / rule_type 为空视为通配。"""
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


def notify_event(event_id: int, client: Optional[httpx.Client] = None) -> int:
    """把事件推送到所有匹配渠道，结果记入 EventAction；返回推送的渠道数。

    同步实现，供 Notifier 线程与「重发通知」API 复用。
    """
    session = get_session()
    own_client = client is None
    if own_client:
        client = httpx.Client()
    try:
        event = session.get(Event, event_id)
        if event is None:
            return 0
        channels = match_channels(session, event)
        if not channels:
            return 0
        payload = event_payload(event)
        for ch in channels:
            try:
                send_webhook(client, ch.webhook, payload)
                result: dict[str, Any] = {"ok": True, "webhook": ch.webhook}
            except Exception as exc:  # noqa: BLE001 单渠道失败不影响其他渠道
                logger.warning("通知渠道 %s 推送失败 event=%d: %s",
                               ch.name, event_id, exc)
                result = {"ok": False, "webhook": ch.webhook,
                          "error": str(exc)[:200]}
            session.add(EventAction(event_id=event_id, action="notify",
                                    actor=ch.name, payload=result))
        session.commit()
        return len(channels)
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
