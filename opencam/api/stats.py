"""统计 API：分时段进出店客流（line_crossing 事件聚合）。

时区一律用服务器本地时间，与规则 active_hours 的解释保持一致。
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Event

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


@router.get("/footfall")
def footfall(
    camera_id: int = Query(...),
    date: Optional[str] = Query(None, description="YYYY-MM-DD，默认今天（本地时区）"),
    session: Session = Depends(session_scope),
):
    """按本地小时分桶统计某摄像头当天的越线进出计数。"""
    date_str, start, end = _day_range(date)
    events = (
        session.query(Event)
        .filter(Event.camera_id == camera_id,
                Event.type == "line_crossing",
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
