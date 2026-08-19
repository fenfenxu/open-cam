"""数据库引擎与会话管理（SQLite）。"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

# 由 init_db 初始化，模块级共享
_engine = None
_SessionLocal: sessionmaker | None = None


def init_db(db_url: str) -> None:
    """初始化引擎并建表，然后做轻量自动迁移。测试可传入 tmp_path 下的库地址。"""
    global _engine, _SessionLocal
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    # 确保模型已注册
    from . import models  # noqa: F401

    Base.metadata.create_all(_engine)
    _migrate(_engine)


def _migrate(engine) -> None:
    """轻量迁移：缺列则 ALTER TABLE 补上，并兜底填充存量数据。"""
    from .models import RULE_TYPE_NAMES

    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("rules")}
        if "name" not in cols:
            conn.execute(text("ALTER TABLE rules ADD COLUMN name VARCHAR(128)"))
        # 存量行（或迁移前插入的行）用类型中文名兜底
        for rule_type, cn_name in RULE_TYPE_NAMES.items():
            conn.execute(
                text("UPDATE rules SET name = :name "
                     "WHERE type = :type AND (name IS NULL OR name = '')"),
                {"name": cn_name, "type": rule_type},
            )


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
