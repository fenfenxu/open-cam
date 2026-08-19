"""新规则单测：line_crossing（方向/防抖）、zone_count、active_hours 时段。

时钟注入用 time.mktime 构造本地时间，不 sleep。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from opencam.detection.rules import RuleEngine, in_active_hours

from .test_rules import FakeClock, FakeRule, det

# 一条竖线：x=100，方向从上到下（y 0→200）。
# 约定：沿线方向看，左手侧（x>100）→右手侧（x<100）为 in，反向为 out。
LINE = [[100, 0], [100, 200]]


def _det_at(cx, cy, track_id=1, cls="person"):
    """底边中点在 (cx, cy) 的检测框。"""
    return det(cx - 10, cy - 40, cx + 10, cy, cls=cls, track_id=track_id)


# ---------- line_crossing ----------

def _line_rule(direction="both", cooldown=0) -> FakeRule:
    return FakeRule(type="line_crossing",
                    params={"line": LINE, "direction": direction},
                    cooldown=cooldown)


def test_line_crossing_both_directions():
    engine = RuleEngine()
    rule = _line_rule()
    # x=50（线方向右侧）→ x=150（线方向左侧）：out
    engine.evaluate([rule], [_det_at(50, 100)])
    hits = engine.evaluate([rule], [_det_at(150, 100)])
    assert len(hits) == 1
    assert hits[0].detail["direction"] == "out"
    assert hits[0].detail["track_id"] == 1
    # 反向穿回：in
    hits = engine.evaluate([rule], [_det_at(50, 100)])
    assert len(hits) == 1
    assert hits[0].detail["direction"] == "in"


def test_line_crossing_direction_filter():
    engine = RuleEngine()
    rule = _line_rule(direction="in")
    engine.evaluate([rule], [_det_at(50, 100)])
    # 右→左 = out，被方向过滤掉
    assert engine.evaluate([rule], [_det_at(150, 100)]) == []
    # 左→右 = in，触发
    hits = engine.evaluate([rule], [_det_at(50, 100)])
    assert hits[0].detail["direction"] == "in"

    engine2 = RuleEngine()
    rule2 = _line_rule(direction="out")
    engine2.evaluate([rule2], [_det_at(50, 100)])
    hits = engine2.evaluate([rule2], [_det_at(150, 100)])
    assert len(hits) == 1
    assert hits[0].detail["direction"] == "out"


def test_line_crossing_same_side_jitter_no_recount():
    engine = RuleEngine()
    rule = _line_rule()
    engine.evaluate([rule], [_det_at(50, 100)])
    hits = engine.evaluate([rule], [_det_at(150, 100)])  # 穿越 → out
    assert len(hits) == 1
    assert hits[0].detail["direction"] == "out"
    # 在一侧来回抖动（含短暂压线 x=100，压线不改变侧别），不重复计数
    for x in (151, 100, 149, 155, 100, 148):
        assert engine.evaluate([rule], [_det_at(x, 100)]) == []
    # 回到原侧再穿过来才再次触发
    assert engine.evaluate([rule], [_det_at(50, 100)])[0].detail["direction"] == "in"
    assert engine.evaluate([rule], [_det_at(150, 100)])[0].detail["direction"] == "out"


def test_line_crossing_ignores_no_track_and_class_filter():
    engine = RuleEngine()
    rule = FakeRule(type="line_crossing",
                    params={"line": LINE, "direction": "both",
                            "classes": ["person"]},
                    cooldown=0)
    # 无 track_id 不参与
    engine.evaluate([rule], [_det_at(50, 100, track_id=None)])
    assert engine.evaluate([rule], [_det_at(150, 100, track_id=None)]) == []
    # 类别被过滤
    engine.evaluate([rule], [_det_at(50, 100, track_id=2, cls="car")])
    assert engine.evaluate([rule], [_det_at(150, 100, track_id=2, cls="car")]) == []


# ---------- zone_count ----------

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]


def test_zone_count_threshold():
    engine = RuleEngine()
    rule = FakeRule(type="zone_count",
                    params={"polygon": SQUARE, "threshold": 3}, cooldown=0)
    inside2 = [_det_at(20, 50, 1), _det_at(40, 50, 2)]
    outside = _det_at(300, 300, 3)
    assert engine.evaluate([rule], inside2 + [outside]) == []
    inside3 = inside2 + [_det_at(60, 50, 4)]
    hits = engine.evaluate([rule], inside3)
    assert len(hits) == 1
    assert hits[0].detail["count"] == 3
    assert hits[0].detail["threshold"] == 3
    assert len(hits[0].detail["objects"]) == 3


def test_zone_count_class_filter():
    engine = RuleEngine()
    rule = FakeRule(type="zone_count",
                    params={"polygon": SQUARE, "threshold": 1,
                            "classes": ["car"]},
                    cooldown=0)
    assert engine.evaluate([rule], [_det_at(50, 50, 1, cls="person")]) == []
    assert len(engine.evaluate([rule], [_det_at(50, 50, 1, cls="car")])) == 1


# ---------- active_hours ----------

def _at(hour, minute=0) -> float:
    """构造今天本地某时刻的 epoch。"""
    return time.mktime((2026, 8, 19, hour, minute, 0, 0, 0, -1))


def test_active_hours_empty_means_all_day():
    assert in_active_hours(None, _at(3))
    assert in_active_hours("", _at(15))


def test_active_hours_normal_range():
    assert in_active_hours("09:00-18:00", _at(9))
    assert in_active_hours("09:00-18:00", _at(17, 59))
    assert not in_active_hours("09:00-18:00", _at(18))
    assert not in_active_hours("09:00-18:00", _at(8, 59))


def test_active_hours_overnight():
    spec = "22:00-07:00"
    assert in_active_hours(spec, _at(22))
    assert in_active_hours(spec, _at(23, 59))
    assert in_active_hours(spec, _at(0))
    assert in_active_hours(spec, _at(6, 59))
    assert not in_active_hours(spec, _at(7))
    assert not in_active_hours(spec, _at(12))


def test_active_hours_bad_spec_fails_open():
    assert in_active_hours("不是时段", _at(12))


def test_active_hours_gates_rule_evaluation():
    # 时段内触发、时段外跳过（时钟注入引擎）
    clock = FakeClock(_at(12))  # 中午，不在 22:00-07:00
    engine = RuleEngine(clock=clock)
    rule = FakeRule(type="zone_intrusion",
                    params={"polygon": SQUARE, "active_hours": "22:00-07:00"},
                    cooldown=0)
    frame = [det(20, 60, 80, 100)]
    assert engine.evaluate([rule], frame) == []
    clock.t = _at(23)  # 进入生效时段
    assert len(engine.evaluate([rule], frame)) == 1
