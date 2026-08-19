"""规则引擎：纯逻辑、可注入时钟，便于单测。

三种规则：
- zone_intrusion：检测框底边中点进入多边形区域即触发。
- loitering：同一 track id 在区域内连续驻留超过 N 秒触发。
- object_count：画面中某类别目标数量达到阈值触发。

每条规则有 cooldown 秒数去抖；规则参数来自 DB 的 JSON 字段。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .detector import Detection


def point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """射线法判断点是否在多边形内。边界上的点视为在内。"""
    x, y = point
    n = len(polygon)
    if n < 3:
        return False
    # 边界判定：点到任一线段距离足够近视为在内
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _on_segment(x: float, y: float, x1: float, y1: float, x2: float, y2: float,
                eps: float = 1e-6) -> bool:
    """点是否在线段上（含端点）。"""
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > eps * max(1.0, abs(x2 - x1), abs(y2 - y1)):
        return False
    return (min(x1, x2) - eps <= x <= max(x1, x2) + eps
            and min(y1, y2) - eps <= y <= max(y1, y2) + eps)


@dataclass
class RuleHit:
    """一次规则命中。"""

    rule_id: int
    rule_type: str
    confidence: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RuleState:
    last_fired_at: float = -1e18
    # loitering：track_id -> 进入区域时间
    track_enter_at: dict[int, float] = field(default_factory=dict)
    # line_crossing：track_id -> 最近一次所在侧（-1/1）
    track_side: dict[int, int] = field(default_factory=dict)


def _line_side(a: list[float], b: list[float], p: tuple[float, float]) -> float:
    """点 p 相对有向线段 a→b 的叉积。>0 在"右侧"，<0 在"左侧"（屏幕坐标系）。

    方向约定：沿线的第一点指向第二点看，左手侧为左、右手侧为右。
    """
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def in_active_hours(spec: Optional[str], now: float) -> bool:
    """规则生效时段判断。spec 形如 "22:00-07:00"，支持跨午夜；空值=全天生效。

    now 为 epoch 秒（按本地时间解释），可注入假时钟测试。
    解析失败时按全天生效处理（宁可多报，不静默禁用规则）。
    """
    if not spec:
        return True
    try:
        start_s, end_s = spec.strip().split("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        start, end = sh * 60 + sm, eh * 60 + em
    except (ValueError, AttributeError):
        return True
    if start == end:
        return True
    lt = time.localtime(now)
    t = lt.tm_hour * 60 + lt.tm_min
    if start < end:
        return start <= t < end
    return t >= start or t < end  # 跨午夜


class RuleEngine:
    """对一帧的检测结果评估全部规则，返回命中列表。

    clock 可注入（默认 time.time），测试用假时钟避免 sleep。
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._states: dict[int, _RuleState] = {}
        self.last_matches: list[RuleHit] = []

    def reset(self, rule_id: Optional[int] = None) -> None:
        if rule_id is None:
            self._states.clear()
        else:
            self._states.pop(rule_id, None)

    def evaluate(self, rules: Iterable, detections: list[Detection]) -> list[RuleHit]:
        """rules 为 Rule ORM 对象或可访问 .id/.type/.params/.cooldown/.enabled 的对象。"""
        now = self._clock()
        hits: list[RuleHit] = []
        self.last_matches = []
        for rule in rules:
            if not rule.enabled:
                continue
            # 生效时段：不在 active_hours 内直接跳过（空值=全天）
            if not in_active_hours(rule.params.get("active_hours"), now):
                continue
            state = self._states.setdefault(rule.id, _RuleState())
            hit = self._eval_one(rule, detections, now, state)
            if hit is None:
                continue
            self.last_matches.append(hit)
            if now - state.last_fired_at < rule.cooldown:
                continue
            state.last_fired_at = now
            hits.append(hit)
        return hits

    # ---- 各规则实现 ----

    def _eval_one(self, rule, detections: list[Detection], now: float,
                  state: _RuleState) -> Optional[RuleHit]:
        if rule.type == "zone_intrusion":
            return self._zone_intrusion(rule, detections)
        if rule.type == "loitering":
            return self._loitering(rule, detections, now, state)
        if rule.type == "object_count":
            return self._object_count(rule, detections)
        if rule.type == "zone_count":
            return self._zone_count(rule, detections)
        if rule.type == "line_crossing":
            return self._line_crossing(rule, detections, state)
        return None

    @staticmethod
    def _filter_classes(detections: list[Detection],
                        classes: Optional[list[str]]) -> list[Detection]:
        if not classes:
            return detections
        return [d for d in detections if d.class_name in classes]

    def _zone_intrusion(self, rule, detections: list[Detection]) -> Optional[RuleHit]:
        polygon = rule.params.get("polygon") or []
        targets = self._filter_classes(detections, rule.params.get("classes"))
        intruders = [d for d in targets if point_in_polygon(d.bottom_center, polygon)]
        if not intruders:
            return None
        best = max(intruders, key=lambda d: d.confidence)
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=best.confidence,
            detail={
                "count": len(intruders),
                "objects": [_det_brief(d) for d in intruders],
            },
        )

    def _loitering(self, rule, detections: list[Detection], now: float,
                   state: _RuleState) -> Optional[RuleHit]:
        polygon = rule.params.get("polygon") or []
        duration = float(rule.params.get("duration", 10))
        targets = self._filter_classes(detections, rule.params.get("classes"))
        inside: dict[int, Detection] = {}
        for d in targets:
            if d.track_id is None:
                continue
            if point_in_polygon(d.bottom_center, polygon):
                inside[d.track_id] = d

        # 区域内的 track 记录进入时间；离开的清除
        for tid in list(state.track_enter_at):
            if tid not in inside:
                del state.track_enter_at[tid]
        for tid in inside:
            state.track_enter_at.setdefault(tid, now)

        loiterers = {
            tid: d for tid, d in inside.items()
            if now - state.track_enter_at[tid] >= duration
        }
        if not loiterers:
            return None
        best = max(loiterers.values(), key=lambda d: d.confidence)
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=best.confidence,
            detail={
                "count": len(loiterers),
                "duration": duration,
                "objects": [_det_brief(d) for d in loiterers.values()],
            },
        )

    def _object_count(self, rule, detections: list[Detection]) -> Optional[RuleHit]:
        cls = rule.params.get("class")
        threshold = int(rule.params.get("threshold", 1))
        matched = [d for d in detections if cls is None or d.class_name == cls]
        if len(matched) < threshold:
            return None
        best = max(matched, key=lambda d: d.confidence)
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=best.confidence,
            detail={
                "count": len(matched),
                "threshold": threshold,
                "class": cls,
                "objects": [_det_brief(d) for d in matched],
            },
        )

    def _zone_count(self, rule, detections: list[Detection]) -> Optional[RuleHit]:
        """区域内人数统计：底边中点在多边形内的目标数达到阈值触发。"""
        polygon = rule.params.get("polygon") or []
        threshold = int(rule.params.get("threshold", 1))
        targets = self._filter_classes(detections, rule.params.get("classes"))
        inside = [d for d in targets if point_in_polygon(d.bottom_center, polygon)]
        if len(inside) < threshold:
            return None
        best = max(inside, key=lambda d: d.confidence)
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=best.confidence,
            detail={
                "count": len(inside),
                "threshold": threshold,
                "objects": [_det_brief(d) for d in inside],
            },
        )

    def _line_crossing(self, rule, detections: list[Detection],
                       state: _RuleState) -> Optional[RuleHit]:
        """越线计数：跟踪目标底边中点相对有向线段的侧别变化。

        方向约定：沿线的第一点指向第二点看，从左侧穿越到右侧记为 "in"，
        反向为 "out"；direction=both 时两个方向都计。
        同一 track 穿越后须回到原侧才能再次触发（侧别状态机天然防抖）。
        """
        line = rule.params.get("line") or []
        if len(line) != 2:
            return None
        direction = rule.params.get("direction", "both")
        targets = self._filter_classes(detections, rule.params.get("classes"))

        crossings: list[dict[str, Any]] = []
        for d in targets:
            if d.track_id is None:
                continue
            side = _line_side(line[0], line[1], d.bottom_center)
            if side == 0:
                continue  # 正好压线，不改变侧别
            s = 1 if side > 0 else -1
            prev = state.track_side.get(d.track_id)
            state.track_side[d.track_id] = s
            if prev is None or prev == s:
                continue
            cross_dir = "in" if s > 0 else "out"  # 左→右为 in
            if direction not in ("both", cross_dir):
                continue
            crossings.append({
                "track_id": d.track_id,
                "direction": cross_dir,
                "object": _det_brief(d),
            })

        if not crossings:
            return None
        best = max(c["object"]["confidence"] for c in crossings)
        return RuleHit(
            rule_id=rule.id,
            rule_type=rule.type,
            confidence=best,
            detail={
                "count": len(crossings),
                "direction": crossings[0]["direction"],
                "track_id": crossings[0]["track_id"],
                "crossings": crossings,
            },
        )


def _det_brief(d: Detection) -> dict[str, Any]:
    return {
        "bbox": [round(v, 1) for v in d.bbox],
        "confidence": round(d.confidence, 3),
        "class_name": d.class_name,
        "track_id": d.track_id,
    }
