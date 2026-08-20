"""统计 API：分时段进出店客流（line_crossing 观察事件聚合）与待办经营摘要。

时区一律用服务器本地时间，与规则 active_hours 的解释保持一致。
客流只统计 intent=observe 的越线；待办摘要按 needs_action 事件行统计，
折叠（repeat_count 增加）不另计新开。
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    EVENT_ACKED,
    EVENT_IGNORED,
    EVENT_OPEN,
    EVENT_RESOLVED,
    INTENT_OBSERVE,
    VERDICT_CONFIRMED,
    VERDICT_FALSE_ALARM,
    VERDICT_UNCLEAR,
    Event,
    EventAction,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _day_range(date: Optional[str]) -> tuple[str, float, float]:
    """把 YYYY-MM-DD 解析为本地当天 [start, end) epoch；空值为今天。"""
    if date:
        try:
            parts = [int(x) for x in date.split("-")]
            if len(parts) != 3:
                raise ValueError
            y, m, d = parts
        except ValueError:
            raise HTTPException(400, f"date 格式应为 YYYY-MM-DD: {date}")
    else:
        lt = time.localtime()
        y, m, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    start = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
    return f"{y:04d}-{m:02d}-{d:02d}", start, start + 86400


@router.get("/footfall", summary="分时段进出店客流", description="按服务器本地时区把当天 line_crossing 观察事件（intent=observe）分 24 小时桶，分别统计 in/out。")
def footfall(
    camera_id: int = Query(..., description="摄像头 ID"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD，默认今天（本地时区）"),
    session: Session = Depends(session_scope),
):
    """按本地小时分桶统计某摄像头当天的越线进出计数（仅观察事件，告警越线不计客流）。"""
    date_str, start, end = _day_range(date)
    events = (
        session.query(Event)
        .filter(Event.camera_id == camera_id,
                Event.type == "line_crossing",
                Event.intent == INTENT_OBSERVE,
                Event.ts >= start, Event.ts < end)
        .all()
    )

    buckets = [{"hour": h, "in": 0, "out": 0} for h in range(24)]
    for event in events:
        hour = time.localtime(event.ts).tm_hour
        # 一条事件可能含多个穿越（同一 tick 多个 track）
        crossings = (event.detail or {}).get("crossings") or [
            {"direction": (event.detail or {}).get("direction")}]
        for crossing in crossings:
            direction = crossing.get("direction")
            if direction in ("in", "out"):
                buckets[hour][direction] += 1

    return {
        "camera_id": camera_id,
        "date": date_str,
        "buckets": buckets,
        "total_in": sum(b["in"] for b in buckets),
        "total_out": sum(b["out"] for b in buckets),
    }


def _avg_transition_sec(session: Session, events: list[Event], to_status: str) -> Optional[float]:
    """每个事件取最早一条 status→to_status 的 EventAction，返回 ts 差值平均。

    没有该流转记录的事件不计入；全部都没有则返回 None。
    """
    deltas: list[float] = []
    for event in events:
        action = (
            session.query(EventAction)
            .filter(EventAction.event_id == event.id,
                    EventAction.action == "status")
            .order_by(EventAction.ts, EventAction.id)
            .all()
        )
        first = next((a for a in action
                      if (a.payload or {}).get("to") == to_status), None)
        if first is not None:
            deltas.append(first.ts - event.ts)
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


@router.get("/ops", summary="待办经营摘要", description="统计当天新建待办（needs_action）的状态分桶、判定分桶与平均确认/处置时长。折叠不另计新开。")
def ops(
    camera_id: Optional[int] = Query(None, description="摄像头 ID，不传为全部"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD，默认今天（本地时区）"),
    session: Session = Depends(session_scope),
):
    """当天 needs_action 事件集合的经营统计：开单数、当前状态桶、判定桶、平均时长。"""
    date_str, start, end = _day_range(date)
    query = session.query(Event).filter(Event.needs_action == True,  # noqa: E712
                                        Event.ts >= start, Event.ts < end)
    if camera_id is not None:
        query = query.filter(Event.camera_id == camera_id)
    events = query.all()

    todos = {"opened": len(events), EVENT_OPEN: 0, EVENT_ACKED: 0,
             EVENT_RESOLVED: 0, EVENT_IGNORED: 0}
    verdicts = {VERDICT_CONFIRMED: 0, VERDICT_FALSE_ALARM: 0,
                VERDICT_UNCLEAR: 0, "none": 0}
    for event in events:
        if event.status in (EVENT_OPEN, EVENT_ACKED, EVENT_RESOLVED, EVENT_IGNORED):
            todos[event.status] += 1
        if event.verdict in (VERDICT_CONFIRMED, VERDICT_FALSE_ALARM, VERDICT_UNCLEAR):
            verdicts[event.verdict] += 1
        else:
            verdicts["none"] += 1

    return {
        "date": date_str,
        "camera_id": camera_id,
        "todos": {"opened": todos["opened"], "open": todos[EVENT_OPEN],
                  "acked": todos[EVENT_ACKED], "resolved": todos[EVENT_RESOLVED],
                  "ignored": todos[EVENT_IGNORED]},
        "verdicts": verdicts,
        "avg_ack_sec": _avg_transition_sec(session, events, EVENT_ACKED),
        "avg_resolve_sec": _avg_transition_sec(session, events, EVENT_RESOLVED),
    }
