"""VLM 复核：异步队列，把事件快照发给 OpenAI 兼容的 chat/completions 接口二次判定。

- api_key 只走环境变量 OPENCAM_VLM_API_KEY；无 key 时事件标记 vlm_status=skipped。
- 超时/失败标 failed，绝不阻塞主链路。
- 成功回填 vlm_verdict(confirmed/false_alarm/uncertain) + vlm_reason。
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
from typing import Optional

import httpx

from ..config import resolve_snapshot_path, settings
from ..db import get_session
from ..models import VLM_DONE, VLM_FAILED, VLM_SKIPPED, Event

logger = logging.getLogger(__name__)

_PROMPT = """你是监控告警复核助手。这条告警类型为「{event_type}」，上下文：{detail}。
请查看快照图片，判断这是否是一条真实有效的告警。
只输出 JSON：{{"verdict": "confirmed" | "false_alarm" | "uncertain", "reason": "一句话理由"}}"""


def review_event(client: httpx.Client, image_path: str, event_type: str,
                 detail: dict) -> tuple[str, str]:
    """调用 VLM 复核一张快照，返回 (verdict, reason)。失败抛异常。"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    prompt = _PROMPT.format(event_type=event_type,
                            detail=json.dumps(detail, ensure_ascii=False)[:500])
    resp = client.post(
        f"{settings.vlm_base_url.rstrip('/')}/chat/completions",
        json={
            "model": settings.vlm_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "temperature": 0,
        },
        timeout=settings.vlm_timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    verdict, reason = _parse_verdict(content)
    return verdict, reason


def _parse_verdict(content: str) -> tuple[str, str]:
    """从模型输出里提取 JSON 判定；解析失败给 uncertain。"""
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
        verdict = data.get("verdict", "uncertain")
        if verdict not in ("confirmed", "false_alarm", "uncertain"):
            verdict = "uncertain"
        return verdict, str(data.get("reason", ""))
    except (ValueError, json.JSONDecodeError):
        return "uncertain", content[:200]


class VlmReviewer:
    """后台线程消费复核队列，结果写回事件表。"""

    def __init__(self):
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vlm-reviewer",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, event_id: int) -> None:
        """把事件放入复核队列；无 api key 直接标 skipped。"""
        if not settings.vlm_api_key:
            self._mark_skipped(event_id)
            return
        self._queue.put(event_id)

    # ---- 内部 ----

    def _run(self) -> None:
        headers = {"Authorization": f"Bearer {settings.vlm_api_key}"}
        with httpx.Client(headers=headers) as client:
            while not self._stop.is_set():
                try:
                    event_id = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._review_one(client, event_id)
                except Exception:  # noqa: BLE001 兜底，保证线程不死
                    logger.exception("VLM 复核事件 %d 出现未处理异常", event_id)
                finally:
                    self._queue.task_done()

    def _review_one(self, client: httpx.Client, event_id: int) -> None:
        session = get_session()
        try:
            event = session.get(Event, event_id)
            if event is None or not event.snapshot_path:
                return
            snapshot = resolve_snapshot_path(event.snapshot_path)
            if not snapshot.exists():
                self._update(session, event, VLM_FAILED, None, "快照文件不存在")
                return
            try:
                verdict, reason = review_event(
                    client, str(snapshot), event.type, event.detail)
                self._update(session, event, VLM_DONE, verdict, reason)
            except Exception as exc:  # 超时/网络/解析失败都不阻塞
                logger.warning("VLM 复核失败 event=%d: %s", event_id, exc)
                self._update(session, event, VLM_FAILED, None, str(exc)[:200])
        finally:
            session.close()

    @staticmethod
    def _update(session, event: Event, status: str, verdict: Optional[str],
                reason: Optional[str]) -> None:
        event.vlm_status = status
        event.vlm_verdict = verdict
        event.vlm_reason = reason
        session.commit()
        logger.info("事件 %d VLM 复核: %s / %s", event.id, status, verdict)

    @staticmethod
    def _mark_skipped(event_id: int) -> None:
        session = get_session()
        try:
            event = session.get(Event, event_id)
            if event is not None:
                event.vlm_status = VLM_SKIPPED
                session.commit()
        finally:
            session.close()


# 全局单例
vlm_reviewer = VlmReviewer()
