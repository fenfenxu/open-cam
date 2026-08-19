"""分时段客流统计 API 测试：跨小时聚合、in/out 分别计数、camera 过滤、空数据、date 参数。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from opencam.db import get_session
from opencam.models import Event


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def _at(hour, minute=0, day=19) -> float:
    """构造本地 2026-08-{day} 某时刻的 epoch。"""
    return time.mktime((2026, 8, day, hour, minute, 0, 0, 0, -1))


def _add_crossing(camera_id: int, ts: float, direction: str, count: int = 1):
    """写一条 line_crossing 事件；count>1 时模拟同一 tick 多个穿越。"""
    crossings = [{"track_id": i + 1, "direction": direction,
                  "object": {"confidence": 0.9}} for i in range(count)]
    session = get_session()
    try:
        session.add(Event(
            camera_id=camera_id, type="line_crossing", confidence=0.9, ts=ts,
            detail={"count": count, "direction": direction,
                    "track_id": 1, "crossings": crossings}))
        session.commit()
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
