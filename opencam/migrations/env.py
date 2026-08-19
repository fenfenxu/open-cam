"""Alembic env：运行时由 migrations.ensure_schema 注入连接；CLI（autogenerate）走 sqlalchemy.url。

CLI 模式未显式给 url 时回退到 opencam.config.settings.db_url，
使 `uv run alembic revision --autogenerate -m "..."` 开箱可用。
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from opencam import models  # noqa: F401 注册全部表
from opencam.db import Base

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    conn = config.attributes.get("connection")
    if conn is not None:
        # 运行时：复用 init_db 传入的连接（事务由外层 engine.begin() 管理）
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    # CLI 模式：自建连接
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        from opencam.config import settings

        url = settings.db_url
    engine = create_engine(url)
    with engine.begin() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
