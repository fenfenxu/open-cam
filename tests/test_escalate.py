"""策略关卡：Escalator 注入时钟 + sqlite，不启动 CaptureWorker。"""

from __future__ import annotations

from opencam.db import get_session, init_db
from opencam.detection.escalate import Escalator
from opencam.detection.rules import RuleHit
from opencam.models import (CAMERA_RUNNING, EVENT_LOGGED, Camera, Event,
                            EventAction, Rule)
from opencam.pipeline import persist_hit


class FakeClock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _session(tmp_settings):
    init_db(tmp_settings.db_url)
    return get_session()


def _camera(session) -> Camera:
    camera = Camera(name="店", source_type="file", source_uri="/tmp/x.mp4",
                    status=CAMERA_RUNNING)
    session.add(camera)
    session.commit()
    return camera


def _zone_rule(session, camera_id: int, escalate: dict) -> Rule:
    rule = Rule(
        camera_id=camera_id, type="zone_count", intent="alert",
        escalate=escalate, cooldown=0,
        params={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "threshold": 1})
    session.add(rule)
    session.commit()
    return rule


def _hit(rule: Rule) -> RuleHit:
    return RuleHit(rule_id=rule.id, rule_type=rule.type, confidence=0.9,
                   detail={"count": 3})


def test_sustained_does_not_write_until_duration(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule_zone_count = _zone_rule(session, camera.id, {
            "mode": "sustained", "fold_open": True,
            "sustained": {"duration_sec": 120},
        })
        clock = FakeClock()
        escalator = Escalator(clock)
        escalator.note_hit(rule_zone_count.id, clock.t)
        d = escalator.decide(session, rule_zone_count, camera_id=camera.id, now=clock.t)
        assert d.open_todo is False and d.write_logged is False
        clock.t += 119
        escalator.note_hit(rule_zone_count.id, clock.t)
        d = escalator.decide(session, rule_zone_count, camera_id=camera.id, now=clock.t)
        assert d.open_todo is False
        clock.t += 1
        escalator.note_hit(rule_zone_count.id, clock.t)
        d = escalator.decide(session, rule_zone_count, camera_id=camera.id, now=clock.t)
        assert d.open_todo is True
    finally:
        session.close()


def test_consecutive_promotes_kth_event(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {
            "mode": "consecutive", "fold_open": True,
            "consecutive": {"count": 3, "window_sec": 600},
        })
        clock = FakeClock()
        escalator = Escalator(clock)
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.write_logged is True and d.open_todo is False
        persist_hit(session, camera.id, rule, _hit(rule), None,
                    escalator=escalator, now=clock.t)
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.write_logged is True and d.open_todo is False
        persist_hit(session, camera.id, rule, _hit(rule), None,
                    escalator=escalator, now=clock.t)
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.open_todo is True
        event = persist_hit(session, camera.id, rule, _hit(rule), None,
                            escalator=escalator, now=clock.t)
        assert event is not None
        assert event.needs_action is True
        assert event.status == "open"
        logged = session.query(Event).filter_by(
            camera_id=camera.id, rule_id=rule.id, needs_action=False).count()
        assert logged == 2
    finally:
        session.close()


def test_fold_does_not_create_second_todo(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {"mode": "immediate", "fold_open": True})
        clock = FakeClock()
        escalator = Escalator(clock)
        first = persist_hit(session, camera.id, rule, _hit(rule), "snapshots/a.jpg",
                            escalator=escalator)
        assert first is not None and first.needs_action is True
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.fold is True
        second = persist_hit(session, camera.id, rule, _hit(rule), "snapshots/b.jpg",
                             escalator=escalator)
        assert second is not None
        assert second.id == first.id
        assert second.repeat_count == 2
        assert (second.detail or {}).get("last_snapshot_path") == "snapshots/b.jpg"
        assert second.snapshot_path == "snapshots/a.jpg"
        todos = session.query(Event).filter_by(
            camera_id=camera.id, needs_action=True).count()
        assert todos == 1
        actions = session.query(EventAction).filter_by(
            event_id=first.id, action="repeat").all()
        assert len(actions) == 1
        assert actions[0].payload["count"] == 2
    finally:
        session.close()


def test_compound_blocks_todo_without_footfall(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {
            "mode": "immediate", "fold_open": True,
            "compound": {"metric": "footfall_in_today", "op": "gte", "value": 200},
        })
        clock = FakeClock()
        escalator = Escalator(clock)
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.open_todo is False
        session.add(Event(
            camera_id=camera.id, type="line_crossing", intent="observe",
            needs_action=False, status=EVENT_LOGGED, ts=clock.t,
            detail={"direction": "in", "count": 200}))
        session.commit()
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.open_todo is True
    finally:
        session.close()


def test_resolved_todo_is_not_fold_target(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {"mode": "immediate", "fold_open": True})
        clock = FakeClock()
        escalator = Escalator(clock)
        first = persist_hit(session, camera.id, rule, _hit(rule), None,
                            escalator=escalator)
        first.status = "resolved"
        session.commit()
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.fold is False
        assert d.open_todo is True
        second = persist_hit(session, camera.id, rule, _hit(rule), None,
                             escalator=escalator)
        assert second is not None
        assert second.id != first.id
        assert second.needs_action is True
    finally:
        session.close()


def test_bad_escalate_json_treated_as_immediate(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {"mode": "nope"})
        clock = FakeClock()
        escalator = Escalator(clock)
        d = escalator.decide(session, rule, camera.id, now=clock.t)
        assert d.open_todo is True
        assert d.fold is False
        assert d.write_logged is False
    finally:
        session.close()


def test_persist_hit_fold_repeat_count(tmp_settings):
    session = _session(tmp_settings)
    try:
        camera = _camera(session)
        rule = _zone_rule(session, camera.id, {})
        escalator = Escalator(FakeClock())
        persist_hit(session, camera.id, rule, _hit(rule), None, escalator=escalator)
        persist_hit(session, camera.id, rule, _hit(rule), None, escalator=escalator)
        todos = session.query(Event).filter_by(
            camera_id=camera.id, needs_action=True).all()
        assert len(todos) == 1
        assert todos[0].repeat_count == 2
    finally:
        session.close()
