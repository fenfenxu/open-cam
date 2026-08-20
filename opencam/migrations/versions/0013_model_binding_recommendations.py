"""模型关联推荐的版本与风险提示。

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "model_bindings" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("model_bindings")}
    if "warnings" not in columns:
        op.add_column(
            "model_bindings",
            sa.Column("warnings", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
        )
    if "model_version_id" not in columns:
        op.add_column(
            "model_bindings",
            sa.Column("model_version_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_model_bindings_model_version_id", "model_bindings", ["model_version_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "model_bindings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("model_bindings")}
    if "model_version_id" in columns:
        op.drop_index("ix_model_bindings_model_version_id", table_name="model_bindings")
        op.drop_column("model_bindings", "model_version_id")
    if "warnings" in columns:
        op.drop_column("model_bindings", "warnings")
