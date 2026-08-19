"""规则引擎单测：几何判定、触发与冷却、驻留计时（注入假时钟）、数量阈值。"""

from __future__ import annotations

from dataclasses import dataclass, field

from opencam.detection.detector import Detection
from opencam.detection.rules import RuleEngine, point_in_polygon

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]


@dataclass
class FakeRule:
    id: int = 1
    type: str = "zone_intrusion"
    params: dict = field(default_factory=dict)
    enabled: bool = True
    cooldown: float = 30.0


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def det(x1, y1, x2, y2, cls="person", track_id=None, conf=0.9) -> Detection:
    return Detection(bbox=(x1, y1, x2, y2), confidence=conf, class_id=0,
                     class_name=cls, track_id=track_id)


# ---------- point_in_polygon ----------

def test_point_inside_polygon():
    assert point_in_polygon((50, 50), SQUARE)


def test_point_outside_polygon():
    assert not point_in_polygon((150, 50), SQUARE)
    assert not point_in_polygon((-10, 50), SQUARE)


def test_point_on_boundary_counts_inside():
    # 边上的点与顶点都视为在内
    assert point_in_polygon((0, 50), SQUARE)
    assert point_in_polygon((100, 100), SQUARE)
    assert point_in_polygon((50, 0), SQUARE)


def test_point_in_concave_polygon():
    # 凹多边形：凹口处的点在外
    concave = [[0, 0], [100, 0], [100, 100], [50, 50], [0, 100]]
    assert point_in_polygon((25, 30), concave)
    assert not point_in_polygon((50, 80), concave)


def test_degenerate_polygon():
    assert not point_in_polygon((0, 0), [[0, 0], [1, 1]])


# ---------- zone_intrusion ----------

def _zone_rule(**kw) -> FakeRule:
    params = {"polygon": SQUARE}
    params.update(kw.pop("params", {}))
    return FakeRule(type="zone_intrusion", params=params, **kw)


def test_zone_intrusion_triggers_on_bottom_center():
    engine = RuleEngine()
    # 底边中点 (50, 100) 在区域内
    hits = engine.evaluate([_zone_rule()], [det(20, 60, 80, 100)])
    assert len(hits) == 1
    assert hits[0].rule_type == "zone_intrusion"
    assert hits[0].detail["count"] == 1


def test_zone_intrusion_uses_bottom_center_not_box_center():
    engine = RuleEngine()
    # 框中心 (50, 130) 在区域外，但底边中点 (50, 160) 更靠下——这里反过来：
    # 框中心在区域内、底边中点在区域外，应不触发
    hits = engine.evaluate([_zone_rule()], [det(20, 60, 80, 140)])
    assert hits == []


def test_zone_intrusion_outside_no_trigger():
    engine = RuleEngine()
    assert engine.evaluate([_zone_rule()], [det(200, 200, 260, 300)]) == []


def test_zone_intrusion_class_filter():
    engine = RuleEngine()
    rule = _zone_rule(params={"classes": ["person"]})
    # car 在区域内但被类别过滤
    assert engine.evaluate([rule], [det(20, 60, 80, 100, cls="car")]) == []


def test_cooldown_debounce():
    clock = FakeClock()
    engine = RuleEngine(clock=clock)
    rule = _zone_rule(cooldown=30.0)
    frame = [det(20, 60, 80, 100)]
    assert len(engine.evaluate([rule], frame)) == 1
    # 冷却期内再次命中不触发
    clock.advance(10)
    assert engine.evaluate([rule], frame) == []
    # 冷却结束后恢复触发
    clock.advance(25)
    assert len(engine.evaluate([rule], frame)) == 1


def test_disabled_rule_never_fires():
    engine = RuleEngine()
    rule = _zone_rule(enabled=False)
    assert engine.evaluate([rule], [det(20, 60, 80, 100)]) == []


# ---------- loitering ----------

def _loiter_rule(duration=10.0, cooldown=30.0) -> FakeRule:
    return FakeRule(type="loitering",
                    params={"polygon": SQUARE, "duration": duration},
                    cooldown=cooldown)


def test_loitering_requires_duration():
    clock = FakeClock()
    engine = RuleEngine(clock=clock)
    rule = _loiter_rule(duration=10.0)
    frame = [det(20, 60, 80, 100, track_id=7)]
    # 刚进入，不足驻留时间
    assert engine.evaluate([rule], frame) == []
    clock.advance(9.9)
    assert engine.evaluate([rule], frame) == []
    clock.advance(0.2)  # 累计 10.1s
    hits = engine.evaluate([rule], frame)
    assert len(hits) == 1
    assert hits[0].detail["objects"][0]["track_id"] == 7


def test_loitering_timer_resets_when_track_leaves():
    clock = FakeClock()
    engine = RuleEngine(clock=clock)
    rule = _loiter_rule(duration=5.0)
    inside = [det(20, 60, 80, 100, track_id=1)]
    outside = [det(200, 200, 260, 300, track_id=1)]
    engine.evaluate([rule], inside)
    clock.advance(4)
    engine.evaluate([rule], outside)  # 离开，计时清零
    clock.advance(4)
    assert engine.evaluate([rule], inside) == []  # 重新进入才 0s
    clock.advance(5.1)
    assert len(engine.evaluate([rule], inside)) == 1


def test_loitering_ignores_detections_without_track_id():
    clock = FakeClock()
    engine = RuleEngine(clock=clock)
    rule = _loiter_rule(duration=1.0)
    frame = [det(20, 60, 80, 100, track_id=None)]
    engine.evaluate([rule], frame)
    clock.advance(10)
    assert engine.evaluate([rule], frame) == []


# ---------- object_count ----------

def test_object_count_threshold():
    engine = RuleEngine()
    rule = FakeRule(type="object_count",
                    params={"class": "person", "threshold": 3})
    two = [det(0, 0, 10, 10), det(20, 0, 30, 10)]
    three = two + [det(40, 0, 50, 10)]
    assert engine.evaluate([rule], two) == []
    hits = engine.evaluate([rule], three)
    assert len(hits) == 1
    assert hits[0].detail["count"] == 3
    assert hits[0].detail["threshold"] == 3


def test_object_count_filters_by_class():
    engine = RuleEngine()
    rule = FakeRule(type="object_count",
                    params={"class": "car", "threshold": 1}, cooldown=0)
    assert engine.evaluate([rule], [det(0, 0, 10, 10, cls="person")]) == []
    assert len(engine.evaluate([rule], [det(0, 0, 10, 10, cls="car")])) == 1
