"""分时段客流统计 API 测试：跨小时聚合、in/out 分别计数、camera 过滤、空数据、date 参数；
待办经营摘要 ops：状态/判定分桶与平均确认、处置时长。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import (
    EVENT_IGNORED,
    EVENT_OPEN,
    EVENT_RESOLVED,
    INTENT_ALERT,
    INTENT_OBSERVE,
    VERDICT_CONFIRMED,
    VERDICT_FALSE_ALARM,
    Event,
    EventAction,
)


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _at(hour, minute=0, day=19) -> float:
    """构造本地 2026-08-{day} 某时刻的 epoch。"""
    return time.mktime((2026, 8, day, hour, minute, 0, 0, 0, -1))


def _add_crossing(camera_id: int, ts: float, direction: str, count: int = 1,
                  intent: str = INTENT_OBSERVE):
    """写一条 line_crossing 事件；count>1 时模拟同一 tick 多个穿越。"""
    crossings = [{"track_id": i + 1, "direction": direction,
                  "object": {"confidence": 0.9}} for i in range(count)]
    session = get_session()
    try:
        session.add(Event(
            camera_id=camera_id, type="line_crossing", confidence=0.9, ts=ts,
            intent=intent, needs_action=intent == INTENT_ALERT,
            detail={"count": count, "direction": direction,
                    "track_id": 1, "crossings": crossings}))
        session.commit()
    finally:
        session.close()


def _add_todo(camera_id: int, ts: float, status: str = EVENT_OPEN,
              verdict: str | None = None,
              transitions: tuple[tuple[str, float], ...] = ()) -> int:
    """写一条待办事件（needs_action=true）；transitions 为 (to 状态, 动作 ts) 序列。"""
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type="zone_intrusion", ts=ts,
                      intent=INTENT_ALERT, needs_action=True,
                      status=status, verdict=verdict, detail={})
        session.add(event)
        session.flush()
        for to, action_ts in transitions:
            session.add(EventAction(
                event_id=event.id, action="status", ts=action_ts,
                payload={"from": EVENT_OPEN, "to": to}))
        session.commit()
        return event.id
    finally:
        session.close()


def test_footfall_hourly_aggregation(client):
    _add_crossing(1, _at(9, 10), "in")
    _add_crossing(1, _at(9, 40), "in")
    _add_crossing(1, _at(9, 50), "out")
    _add_crossing(1, _at(18, 5), "out", count=2)  # 一条事件两个穿越

    resp = client.get("/api/stats/footfall",
                      params={"camera_id": 1, "date": "2026-08-19"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["camera_id"] == 1
    assert body["date"] == "2026-08-19"
    assert len(body["buckets"]) == 24

    b9 = body["buckets"][9]
    assert b9 == {"hour": 9, "in": 2, "out": 1}
    b18 = body["buckets"][18]
    assert b18 == {"hour": 18, "in": 0, "out": 2}
    assert body["total_in"] == 2
    assert body["total_out"] == 3
    # 其他小时为空
    assert all(b["in"] == 0 and b["out"] == 0
               for i, b in enumerate(body["buckets"]) if i not in (9, 18))


def test_footfall_filters_camera_and_type(client):
    _add_crossing(1, _at(10), "in")
    _add_crossing(2, _at(10), "in")          # 其他摄像头
    # 非 line_crossing 事件不计入
    session = get_session()
    try:
        session.add(Event(camera_id=1, type="zone_intrusion", ts=_at(10),
                          detail={}))
        session.commit()
    finally:
        session.close()

    body = client.get("/api/stats/footfall",
                      params={"camera_id": 1, "date": "2026-08-19"}).json()
    assert body["total_in"] == 1
    assert body["total_out"] == 0


def test_footfall_date_range_excludes_other_days(client):
    _add_crossing(1, _at(10, day=18), "in")  # 昨天
    _add_crossing(1, _at(10, day=19), "in")  # 当天

    body = client.get("/api/stats/footfall",
                      params={"camera_id": 1, "date": "2026-08-19"}).json()
    assert body["total_in"] == 1
    body18 = client.get("/api/stats/footfall",
                        params={"camera_id": 1, "date": "2026-08-18"}).json()
    assert body18["total_in"] == 1
    assert body18["date"] == "2026-08-18"


def test_footfall_empty(client):
    body = client.get("/api/stats/footfall",
                      params={"camera_id": 99, "date": "2026-08-19"}).json()
    assert body["total_in"] == 0
    assert body["total_out"] == 0
    assert len(body["buckets"]) == 24


def test_footfall_default_date_is_today(client):
    resp = client.get("/api/stats/footfall", params={"camera_id": 1})
    assert resp.status_code == 200
    lt = time.localtime()
    assert resp.json()["date"] == f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"


def test_footfall_bad_date(client):
    resp = client.get("/api/stats/footfall",
                      params={"camera_id": 1, "date": "昨天"})
    assert resp.status_code == 400


def test_footfall_requires_camera_id(client):
    resp = client.get("/api/stats/footfall")
    assert resp.status_code == 422


def test_footfall_ignores_alert_line_crossing(client):
    """客流只统计 intent=observe 的越线，告警越线不计入。"""
    _add_crossing(1, _at(10), "in")                            # 观察，计入
    _add_crossing(1, _at(11), "in", intent=INTENT_ALERT)       # 告警，不计入

    body = client.get("/api/stats/footfall",
                      params={"camera_id": 1, "date": "2026-08-19"}).json()
    assert body["total_in"] == 1
    assert body["total_out"] == 0


# ---------- ops：待办经营摘要 ----------

def test_ops_counts_todos_and_verdicts(client):
    ts = _at(9)
    # 属实且已处置：9:00 开单，60s 后确认，300s 后处置完成
    _add_todo(1, ts, status=EVENT_RESOLVED, verdict=VERDICT_CONFIRMED,
              transitions=(("acked", ts + 60), ("resolved", ts + 300)))
    # 误报忽略：无任何处置动作
    _add_todo(1, _at(10), status=EVENT_IGNORED, verdict=VERDICT_FALSE_ALARM)
    # 观察记录不算待办
    _add_crossing(1, _at(11), "in")

    body = client.get("/api/stats/ops", params={"date": "2026-08-19"}).json()
    assert body["date"] == "2026-08-19"
    assert body["camera_id"] is None
    assert body["todos"] == {"opened": 2, "open": 0, "acked": 0,
                             "resolved": 1, "ignored": 1}
    assert body["verdicts"] == {"confirmed": 1, "false_alarm": 1,
                                "unclear": 0, "none": 0}
    assert body["avg_ack_sec"] == 60.0
    assert body["avg_resolve_sec"] == 300.0


def test_ops_avg_null_when_no_transitions(client):
    """全部待办都没有 ack/resolved 流转记录时，平均时长为 null。"""
    _add_todo(1, _at(9))
    _add_todo(1, _at(10))

    body = client.get("/api/stats/ops", params={"date": "2026-08-19"}).json()
    assert body["todos"] == {"opened": 2, "open": 2, "acked": 0,
                             "resolved": 0, "ignored": 0}
    assert body["verdicts"]["none"] == 2
    assert body["avg_ack_sec"] is None
    assert body["avg_resolve_sec"] is None


def test_ops_filters_camera(client):
    _add_todo(1, _at(9))
    _add_todo(2, _at(9))

    body = client.get("/api/stats/ops",
                      params={"camera_id": 1, "date": "2026-08-19"}).json()
    assert body["camera_id"] == 1
    assert body["todos"]["opened"] == 1
