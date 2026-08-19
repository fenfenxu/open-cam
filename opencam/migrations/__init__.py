"""数据库 schema 版本化迁移（基于 Alembic，运行时代码调用，不依赖 alembic.ini）。

升级安全约定（详见 AGENTS.md「升级与数据安全」）：
- 所有 schema 变更必须走 versions/ 下的版本脚本，禁止运行期手写 ALTER 补丁。
- init_db 流程：全新库 → create_all + stamp head；
  无 alembic_version 的存量库（≤0.2.0）→ 旧补丁 _legacy_fixup + stamp 基线 → upgrade head。
- 任何实际发生的跨版本升级都先把 SQLite 文件复制到 backups/，失败自动还原。
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent
# 基线版本：等价于 0.2.0 的 schema（rules 表已含 name 列）
BASELINE_REVISION = "0001"


def make_config(db_url: str = "sqlite://",
                script_location: Optional[Path] = None) -> Config:
    """构造 Alembic Config；script_location 参数主要给测试用（临时迁移目录）。"""
    cfg = Config()
    cfg.set_main_option("script_location",
                        str(script_location or MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def head_revision(script_location: Optional[Path] = None) -> str:
    return ScriptDirectory.from_config(make_config(script_location=script_location)).get_current_head()


def current_revision(engine: Engine) -> Optional[str]:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _run_with_connection(engine: Engine, db_url: str, fn, *args,
                         script_location: Optional[Path] = None) -> None:
    """在 engine 连接上执行 alembic 命令（env.py 从 attributes 取连接）。"""
    with engine.begin() as conn:
        cfg = make_config(db_url, script_location)
        cfg.attributes["connection"] = conn
        fn(cfg, *args)


def stamp(engine: Engine, revision: str, db_url: str = "sqlite://",
          script_location: Optional[Path] = None) -> None:
    _run_with_connection(engine, db_url, command.stamp, revision,
                         script_location=script_location)


def upgrade_head(engine: Engine, db_url: str,
                 script_location: Optional[Path] = None) -> None:
    _run_with_connection(engine, db_url, command.upgrade, "head",
                         script_location=script_location)


def db_path_from_url(db_url: str) -> Optional[Path]:
    """sqlite:///path → Path；内存库返回 None（无法按文件备份）。"""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    path = db_url[len(prefix):]
    if path == ":memory:":
        return None
    return Path(path)


def backup_db_file(db_url: str, backup_dir: Path, from_revision: str) -> Optional[Path]:
    """迁移前备份 SQLite 文件，返回备份路径；内存库/库文件不存在时返回 None。"""
    src = db_path_from_url(db_url)
    if src is None or not src.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"opencam-v{from_revision}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(src, dest)
    logger.info("迁移前备份数据库: %s -> %s", src, dest)
    return dest


def _restore_db_file(engine: Engine, backup: Path) -> None:
    """迁移失败回滚：断开连接后用备份文件覆盖回去。"""
    dest = db_path_from_url(str(engine.url))
    engine.dispose()
    shutil.copy2(backup, dest)
    logger.warning("迁移失败，已从备份还原: %s -> %s", backup, dest)


def _legacy_fixup(engine: Engine) -> None:
    """Alembic 引入前存量库的旧补丁（仅此一段，不再扩展；之后一律走版本脚本）。

    职责只两件：补建缺失的表（create_all 不动已有表）；
    补 rules.name 列并用类型中文名兜底（基线 0001 已含该列，无版本脚本负责它）。
    其余缺列由 stamp 基线后的 upgrade 链（0002、0003…）幂等补齐。
    """
    from ..db import Base
    from ..models import RULE_TYPE_NAMES

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("rules")}
        if "name" not in cols:
            conn.execute(text("ALTER TABLE rules ADD COLUMN name VARCHAR(128)"))
        for rule_type, cn_name in RULE_TYPE_NAMES.items():
            conn.execute(
                text("UPDATE rules SET name = :name "
                     "WHERE type = :type AND (name IS NULL OR name = '')"),
                {"name": cn_name, "type": rule_type},
            )


def verify_schema(engine: Engine,
                  script_location: Optional[Path] = None) -> list[str]:
    """升级质检：返回问题列表，空列表 = 通过。"""
    from ..db import Base

    problems: list[str] = []
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA integrity_check")).scalar()
        if result != "ok":
            problems.append(f"SQLite 完整性检查失败: {result}")
        tables = set(inspect(conn).get_table_names())
    # 必备表直接取当前模型定义，随模型演进自动更新
    for table in Base.metadata.tables:
        if table not in tables:
            problems.append(f"缺少数据表: {table}")
    current = current_revision(engine)
    head = head_revision(script_location)
    if current != head:
        problems.append(f"schema 版本落后: 当前 {current}，最新 {head}")
    return problems


def ensure_schema(engine: Engine, db_url: str,
                  backup_dir: Optional[Path] = None,
                  script_location: Optional[Path] = None) -> None:
    """把库结构升到最新版本：全新建库 / 存量库版本化迁移；升级前备份、失败还原。"""
    from ..db import Base  # 延迟 import 避免循环

    tables = set(inspect(engine).get_table_names())
    if not tables:
        # 全新库：直接按当前模型建表，标记为最新版本
        Base.metadata.create_all(engine)
        stamp(engine, "head", db_url, script_location=script_location)
        logger.info("新建数据库并标记 schema 版本 %s", head_revision(script_location))
        return

    if "alembic_version" not in tables:
        # ≤0.2.0 的存量库：先跑旧补丁，再标记到基线版本
        _legacy_fixup(engine)
        stamp(engine, BASELINE_REVISION, db_url, script_location=script_location)
        logger.info("存量库已接入版本化迁移（基线 %s）", BASELINE_REVISION)

    current = current_revision(engine)
    head = head_revision(script_location)
    if current == head:
        return

    # 跨版本升级：先备份，失败自动还原
    backup = None
    if backup_dir is not None:
        backup = backup_db_file(db_url, backup_dir, current or "unknown")
    logger.info("数据库 schema 升级: %s -> %s", current, head)
    try:
        upgrade_head(engine, db_url, script_location=script_location)
        problems = verify_schema(engine, script_location)
        if problems:
            raise RuntimeError("升级后质检未通过: " + "; ".join(problems))
    except Exception:
        if backup is not None:
            _restore_db_file(engine, backup)
        raise
