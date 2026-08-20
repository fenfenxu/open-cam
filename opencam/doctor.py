"""升级质检与运行健康检查。

两个入口：
- 服务启动（lifespan）：init_db 后跑 verify_startup，有问题直接拒绝启动（fail fast）。
- 运行期：GET /api/system/health / `opencam system doctor` 跑 check_health 全量检查。
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

from . import migrations
from .config import resolve_snapshot_path, settings
from .db import get_session
from .models import Event

logger = logging.getLogger(__name__)


def _engine():
    return get_session().get_bind()


def verify_startup() -> None:
    """启动自检：schema 版本/完整性不合格直接抛异常，阻止带病启动。"""
    problems = migrations.verify_schema(_engine())
    if problems:
        raise RuntimeError(
            "数据库启动自检未通过: " + "; ".join(problems)
            + "。改动对照：make dev-status"
        )


def _check_dir_writable(path) -> str | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return None
    except OSError as exc:
        return f"目录不可写 {path}: {exc}"


def check_health(sample_snapshots: int = 5) -> dict[str, Any]:
    """全量质检：schema 版本、完整性、目录可写、近期事件快照文件是否还在。"""
    checks: dict[str, Any] = {}

    problems = migrations.verify_schema(_engine())
    checks["schema"] = {
        "ok": not problems,
        "problems": problems,
        "revision": migrations.current_revision(_engine()),
        "head": migrations.head_revision(),
    }

    data_dir_problem = _check_dir_writable(settings.data_dir)
    snap_dir_problem = _check_dir_writable(settings.snapshot_dir)
    checks["data_dir"] = {"ok": data_dir_problem is None,
                          "path": str(settings.data_dir), "problem": data_dir_problem}
    checks["snapshot_dir"] = {"ok": snap_dir_problem is None,
                              "path": str(settings.snapshot_dir),
                              "problem": snap_dir_problem}

    # 抽查最近若干条带快照的事件，确认文件没丢
    session = get_session()
    try:
        events = (session.query(Event)
                  .filter(Event.snapshot_path.isnot(None))
                  .order_by(Event.ts.desc()).limit(sample_snapshots).all())
        missing = [e.id for e in events
                   if not resolve_snapshot_path(e.snapshot_path).exists()]
    finally:
        session.close()
    checks["snapshots"] = {"ok": not missing, "sampled": len(events),
                           "missing_event_ids": missing}

    checks["ok"] = all(c["ok"] for c in checks.values())
    return checks
