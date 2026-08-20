"""模型版本登记、A/B 指标对比、部署与回滚。

线上槽位按任务定义的 object+property 划分：同一场景只保留一个 live，
以及一个 previous 作为常驻回滚目标。部署默认要求候选在三项指标上
全面更优；未更优时拒绝替换（force=true 可强行上线）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    MODEL_LIVE,
    MODEL_PREVIOUS,
    MODEL_REGISTERED,
    MODEL_RETIRED,
    ModelVersion,
)
from .define import _as_metrics
from .storage import load_definition, task_dir, task_exists

# 越高越好 / 越低越好；三项都严格更优才建议替换
_HIGHER_BETTER = ("accuracy", "recall")
_LOWER_BETTER = ("false_alarm_per_day",)


class RegistryError(ValueError):
    """可映射为 HTTP 4xx 的登记/部署错误。"""

    def __init__(self, message: str, status_code: int = 400,
                 payload: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def slot_key_from_definition(definition: dict[str, Any]) -> str:
    obj = str(definition.get("object") or "目标").strip() or "目标"
    prop = str(definition.get("property") or "状态").strip() or "状态"
    return f"{obj}:{prop}"


def compare_metrics(candidate: dict[str, Any],
                    live: Optional[dict[str, Any]]) -> dict[str, Any]:
    """对比候选与线上模型。无线上版本时一律建议部署。"""
    cand = _as_metrics(candidate)
    details: dict[str, Any] = {}
    if live is None:
        for name in (*_HIGHER_BETTER, *_LOWER_BETTER):
            details[name] = {
                "candidate": cand[name],
                "live": None,
                "better": True,
                "worse": False,
                "tied": False,
            }
        return {
            "recommend_replace": True,
            "reason": "当前槽位没有线上模型，可以部署",
            "metrics": details,
        }
    online = _as_metrics(live)
    all_better = True
    for name in _HIGHER_BETTER:
        c, l = cand[name], online[name]
        better, worse, tied = c > l, c < l, c == l
        if not better:
            all_better = False
        details[name] = {
            "candidate": c, "live": l,
            "better": better, "worse": worse, "tied": tied,
        }
    for name in _LOWER_BETTER:
        c, l = cand[name], online[name]
        better, worse, tied = c < l, c > l, c == l
        if not better:
            all_better = False
        details[name] = {
            "candidate": c, "live": l,
            "better": better, "worse": worse, "tied": tied,
        }
    if all_better:
        reason = "指标全面更优，建议替换线上模型"
    else:
        reason = "未全面更优，不建议替换（回滚入口仍可用）"
    return {
        "recommend_replace": all_better,
        "reason": reason,
        "metrics": details,
    }


def _safe_artifact(path: Path) -> Path:
    """产物必须落在 data_dir 下，避免路径穿越。"""
    resolved = path.resolve()
    root = settings.data_dir.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise RegistryError("产物路径必须位于数据目录内") from None
    if not resolved.is_file():
        raise RegistryError(f"模型产物不存在: {resolved}")
    return resolved


def _load_eval_metrics(task_id: str) -> Optional[dict[str, Any]]:
    path = task_dir(task_id) / "eval.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return _as_metrics(data.get("metrics") if isinstance(data.get("metrics"), dict)
                       else data)


def _write_slot_pointer(slot_key: str, live: Optional[ModelVersion],
                        previous: Optional[ModelVersion]) -> None:
    dest_dir = settings.data_dir / "models" / "slots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in slot_key)
    payload = {
        "slot_key": slot_key,
        "live_id": None if live is None else live.id,
        "previous_id": None if previous is None else previous.id,
        "artifact_path": None if live is None else live.artifact_path,
        "updated_at": time.time(),
    }
    (dest_dir / f"{safe}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def live_of(session: Session, slot_key: str) -> Optional[ModelVersion]:
    return (session.query(ModelVersion)
            .filter_by(slot_key=slot_key, status=MODEL_LIVE)
            .one_or_none())


def previous_of(session: Session, slot_key: str) -> Optional[ModelVersion]:
    return (session.query(ModelVersion)
            .filter_by(slot_key=slot_key, status=MODEL_PREVIOUS)
            .one_or_none())


def register_version(session: Session, task_id: str,
                     metrics: Optional[dict[str, Any]] = None,
                     artifact_path: Optional[str] = None,
                     model_asset_id: Optional[int] = None,
                     framework: Optional[str] = None,
                     runtime: Optional[str] = None,
                     input_size: Optional[int] = None) -> ModelVersion:
    if not task_exists(task_id):
        raise RegistryError("训练任务不存在", 404)
    definition = load_definition(task_id)
    slot = slot_key_from_definition(definition)
    raw_metrics = metrics if metrics is not None else _load_eval_metrics(task_id)
    if raw_metrics is None:
        raise RegistryError("缺少评估指标：请传入 metrics 或写入 eval.json")
    normalized = _as_metrics(raw_metrics)
    dest = Path(artifact_path) if artifact_path else task_dir(task_id) / "best.pt"
    if not dest.is_absolute():
        dest = (settings.data_dir / dest).resolve()
    dest = _safe_artifact(dest)
    from ..model_assets import sha256_file
    row = ModelVersion(
        task_id=task_id,
        model_asset_id=model_asset_id,
        slot_key=slot,
        artifact_path=str(dest),
        artifact_hash=sha256_file(dest),
        framework=framework,
        runtime=runtime,
        input_size=input_size,
        metrics=normalized,
        created_at=time.time(),
        status=MODEL_REGISTERED,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def comparison_for(session: Session, version: ModelVersion) -> dict[str, Any]:
    live = live_of(session, version.slot_key)
    live_metrics = None if live is None or live.id == version.id else live.metrics
    result = compare_metrics(version.metrics, live_metrics)
    return {
        **result,
        "candidate_id": version.id,
        "live_id": None if live is None else live.id,
        "slot_key": version.slot_key,
    }


def _promote(session: Session, version: ModelVersion) -> ModelVersion:
    slot = version.slot_key
    current_live = live_of(session, slot)
    current_prev = previous_of(session, slot)
    if current_live is not None and current_live.id == version.id:
        return version
    if current_prev is not None and current_prev.id != version.id:
        current_prev.status = MODEL_RETIRED
    if current_live is not None:
        current_live.status = MODEL_PREVIOUS
    version.status = MODEL_LIVE
    session.commit()
    session.refresh(version)
    _write_slot_pointer(slot, version, previous_of(session, slot))
    return version


def deploy_version(session: Session, version_id: int, *,
                   force: bool = False) -> dict[str, Any]:
    version = session.get(ModelVersion, version_id)
    if version is None:
        raise RegistryError("模型版本不存在", 404)
    cmp = comparison_for(session, version)
    prev = previous_of(session, version.slot_key)
    prev_id = None if prev is None else prev.id
    if version.status == MODEL_LIVE:
        return {
            "deployed": True,
            "already_live": True,
            "force": force,
            **cmp,
            "model": version,
            "previous_id": prev_id,
        }
    if not cmp["recommend_replace"] and not force:
        raise RegistryError(cmp["reason"], 409, payload=cmp)
    promoted = _promote(session, version)
    new_prev = previous_of(session, promoted.slot_key)
    return {
        "deployed": True,
        "already_live": False,
        "force": force,
        **cmp,
        "model": promoted,
        "previous_id": None if new_prev is None else new_prev.id,
    }


def rollback_slot(session: Session, version_id: int) -> dict[str, Any]:
    version = session.get(ModelVersion, version_id)
    if version is None:
        raise RegistryError("模型版本不存在", 404)
    prev = previous_of(session, version.slot_key)
    if prev is None:
        raise RegistryError("没有可回滚的上一版本")
    demoted = live_of(session, version.slot_key)
    restored = _promote(session, prev)
    new_prev = previous_of(session, restored.slot_key)
    cmp = compare_metrics(
        restored.metrics, None if demoted is None else demoted.metrics)
    return {
        "deployed": True,
        "already_live": False,
        "rolled_back": True,
        "force": False,
        "recommend_replace": True,
        "reason": "已回滚到上一线上版本",
        "metrics": cmp["metrics"],
        "candidate_id": restored.id,
        "live_id": restored.id,
        "slot_key": restored.slot_key,
        "model": restored,
        "previous_id": None if new_prev is None else new_prev.id,
    }


def list_versions(session: Session, task_id: Optional[str] = None,
                  slot_key: Optional[str] = None) -> list[ModelVersion]:
    q = session.query(ModelVersion)
    if task_id:
        q = q.filter_by(task_id=task_id)
    if slot_key:
        q = q.filter_by(slot_key=slot_key)
    return q.order_by(ModelVersion.id.desc()).all()
