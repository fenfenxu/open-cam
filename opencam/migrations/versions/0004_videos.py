"""videos 表：本地素材库（文件源登记与回放）。

0003 引入时漏了这张表的迁移脚本，导致存量库升级后缺表（被启动质检拦下）。
本脚本补上；幂等，已被 create_all 建过的新库不会重复执行。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "videos" in inspector.get_table_names():
        return
    op.create_table(
        "videos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("path"),
    )


def downgrade() -> None:
    op.drop_table("videos")
