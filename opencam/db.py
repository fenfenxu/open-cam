"""数据库引擎与会话管理（SQLite + Alembic 版本化迁移）。

schema 变更一律走 opencam/migrations/ 版本脚本；init_db 只负责建引擎、
建/升库（含迁移前备份与失败回滚），具体流程见 migrations.ensure_schema。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

# 由 init_db 初始化，模块级共享
_engine = None
_SessionLocal: sessionmaker | None = None


def init_db(db_url: str, backup_dir: Optional[Path] = None) -> None:
    """初始化引擎并把库结构升到最新版本。测试可传入 tmp_path 下的库地址。

    backup_dir：跨版本升级前的备份目录（生产调用方应传入，通常为 data_dir/backups）。
    """
    global _engine, _SessionLocal
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    # 确保模型已注册
    from . import migrations, models  # noqa: F401

    migrations.ensure_schema(_engine, db_url, backup_dir)
    from .model_assets import ensure_builtin_assets
    session = get_session()
    try:
        ensure_builtin_assets(session)
    finally:
        session.close()


def get_session() -> Session:
    """新建一个会话；调用方负责 close。"""
    if _SessionLocal is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db()")
    return _SessionLocal()


def session_scope():
    """FastAPI 依赖注入用的会话生成器。"""
    session = get_session()
    try:
        yield session
    finally:
        session.close()
