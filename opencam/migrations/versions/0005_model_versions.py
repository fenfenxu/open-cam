"""model_versions 表：训练产物版本登记。

训练模型写进 ORM 时漏了迁移脚本，导致已 stamp 到 0004 的存量库缺表
（被启动质检拦下）。本脚本补上；幂等，已被 create_all 建过的新库不会重复执行。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "model_versions" in inspector.get_table_names():
        return
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), nullable=False, index=True),
        sa.Column("slot_key", sa.String(128), nullable=False, index=True),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("model_versions")
