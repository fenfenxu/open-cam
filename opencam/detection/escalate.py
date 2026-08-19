"""告警升格：immediate / sustained / consecutive / compound + 待办折叠。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from ..models import (EVENT_ACKED, EVENT_LOGGED, EVENT_OPEN, INTENT_OBSERVE,
                      Event)

logger = logging.getLogger(__name__)

VALID_MODES = ("immediate", "sustained", "consecutive")
COMPOUND_METRIC = "footfall_in_today"
COMPOUND_OP = "gte"


@dataclass
class EscalateDecision:
    write_logged: bool  # consecutive 未满 K：写 logged
    open_todo: bool  # 打开或升格待办
    fold: bool  # 并入已有未结待办


@dataclass
class EscalateConfig:
    mode: str = "immediate"
    fold_open: bool = True
    duration_sec: float = 120.0
    consecutive_count: int = 3
    window_sec: float = 600.0
    compound_value: Optional[int] = None


def validate_escalate_payload(escalate: dict[str, Any] | None) -> None:
    """规则 POST/PUT 用。非法时 ValueError，文案含 escalate。"""
    if not escalate:
        return
    if not isinstance(escalate, dict):
        raise ValueError("escalate 必须是对象")
    mode = escalate.get("mode")
    if mode is not None and mode not in VALID_MODES:
        raise ValueError(f"escalate.mode 非法: {mode}")
    compound = escalate.get("compound")
    if compound is None:
        return
    if not isinstance(compound, dict):
        raise ValueError("escalate.compound 非法")
    if compound.get("metric") != COMPOUND_METRIC or compound.get("op") != COMPOUND_OP:
        raise ValueError("escalate.compound.metric/op 非法")
    value = compound.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("escalate.compound.value 非法")


def parse_escalate(raw: Any) -> EscalateConfig:
    """损坏 JSON 视为 immediate + fold_open，并打 warning。"""
    if not isinstance(raw, dict):
        logger.warning("escalate JSON 损坏: %r，视为 immediate+fold_open", raw)
        return EscalateConfig()
    mode = raw.get("mode") or "immediate"
    if mode not in VALID_MODES:
        logger.warning("escalate JSON 损坏 mode=%r，视为 immediate+fold_open", mode)
        return EscalateConfig()
    fold_open = raw.get("fold_open", True)
    if not isinstance(fold_open, bool):
        fold_open = True
    cfg = EscalateConfig(mode=mode, fold_open=fold_open)
    sustained = raw.get("sustained") if isinstance(raw.get("sustained"), dict) else {}
    consecutive = raw.get("consecutive") if isinstance(raw.get("consecutive"), dict) else {}
    try:
        if "duration_sec" in sustained:
            cfg.duration_sec = float(sustained["duration_sec"])
    except (TypeError, ValueError):
        pass
    try:
        if "count" in consecutive:
            cfg.consecutive_count = int(consecutive["count"])
        if "window_sec" in consecutive:
            cfg.window_sec = float(consecutive["window_sec"])
    except (TypeError, ValueError):
        pass
    compound = raw.get("compound")
    if isinstance(compound, dict):
        if (compound.get("metric") == COMPOUND_METRIC
                and compound.get("op") == COMPOUND_OP):
            try:
                value = compound.get("value")
                if not isinstance(value, bool) and isinstance(value, (int, float)):
                    cfg.compound_value = int(value)
            except (TypeError, ValueError):
                pass
        else:
            logger.warning("escalate.compound 损坏，忽略复合条件: %r", compound)
    return cfg


def find_open_todo(session: Session, camera_id: int, rule_id: int) -> Optional[Event]:
    """同摄像头+规则、未结案的待办（open/acked）。"""
    return (
        session.query(Event)
        .filter(
            Event.camera_id == camera_id,
            Event.rule_id == rule_id,
            Event.needs_action.is_(True),
            Event.status.in_((EVENT_OPEN, EVENT_ACKED)),
        )
        .order_by(Event.ts.desc())
        .first()
    )


def footfall_in_today(session: Session, camera_id: int, now: float) -> int:
    """当日 0 点至今、observe 越线的进店数（与 footfall 同一套 crossing 展开）。"""
    lt = time.localtime(now)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    events = (
        session.query(Event)
        .filter(
            Event.camera_id == camera_id,
            Event.type == "line_crossing",
            Event.intent == INTENT_OBSERVE,
            Event.ts >= start,
            Event.ts <= now,
        )
        .all()
    )
    total = 0
    for event in events:
        detail = event.detail or {}
        crossings = detail.get("crossings") or [{
            "direction": detail.get("direction"),
            "count": detail.get("count", 1),
        }]
        for crossing in crossings:
            if crossing.get("direction") != "in":
                continue
            try:
                total += int(crossing.get("count") or 1)
            except (TypeError, ValueError):
                total += 1
    return total


class Escalator:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._since: dict[int, float] = {}

    def decide(self, session: Session, rule, camera_id: int,
               now: float | None = None) -> EscalateDecision:
        """compound → mode → fold。escalate JSON 损坏时 warning 并视为 immediate+fold_open。"""
        now = self._clock() if now is None else now
        cfg = parse_escalate(getattr(rule, "escalate", None) or {})
        compound_ok = True
        if cfg.compound_value is not None:
            compound_ok = footfall_in_today(session, camera_id, now) >= cfg.compound_value
        existing = (
            find_open_todo(session, camera_id, rule.id) if cfg.fold_open else None)

        if cfg.mode == "sustained":
            if not compound_ok:
                return EscalateDecision(False, False, False)
            since = self._since.get(rule.id)
            if since is None or now - since < cfg.duration_sec:
                return EscalateDecision(False, False, False)
            self._since.pop(rule.id, None)
            if existing is not None:
                return EscalateDecision(False, True, True)
            return EscalateDecision(False, True, False)

        if cfg.mode == "consecutive":
            window_start = now - cfg.window_sec
            n = (
                session.query(Event)
                .filter(
                    Event.camera_id == camera_id,
                    Event.rule_id == rule.id,
                    Event.ts >= window_start,
                    Event.ts <= now,
                )
                .count()
                + 1
            )
            if n < cfg.consecutive_count:
                return EscalateDecision(True, False, False)
            if not compound_ok:
                return EscalateDecision(True, False, False)
            if existing is not None:
                return EscalateDecision(False, True, True)
            return EscalateDecision(True, True, False)

        # immediate
        if not compound_ok:
            return EscalateDecision(False, False, False)
        if existing is not None:
            return EscalateDecision(False, True, True)
        return EscalateDecision(False, True, False)

    def note_hit(self, rule_id: int, now: float) -> None:
        """本帧该规则检测命中，供 sustained 计时。"""
        self._since.setdefault(rule_id, now)

    def note_miss(self, rule_id: int) -> None:
        """本帧该规则未命中，sustained 清零。"""
        self._since.pop(rule_id, None)
