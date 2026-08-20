"""模型资产与业务对象关联。

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "model_assets" not in tables:
        op.create_table(
            "model_assets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_type", sa.String(24), nullable=False),
            sa.Column("model_kind", sa.String(32), nullable=False),
            sa.Column("task_key", sa.String(128), nullable=True),
            sa.Column("solution_pack_id", sa.String(128), nullable=True),
            sa.Column("training_task_id", sa.String(64), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_model_assets_source_type", "model_assets", ["source_type"])
        op.create_index("ix_model_assets_model_kind", "model_assets", ["model_kind"])
        op.create_index("ix_model_assets_task_key", "model_assets", ["task_key"])
        op.create_index("ix_model_assets_status", "model_assets", ["status"])

    if "model_bindings" not in tables:
        op.create_table(
            "model_bindings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("model_asset_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(24), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("target_key", sa.String(128), nullable=True),
            sa.Column("relation_source", sa.String(24), nullable=False,
                      server_default="manual"),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["model_asset_id"], ["model_assets.id"],
                                    ondelete="CASCADE"),
        )
        op.create_index("ix_model_bindings_model_asset_id", "model_bindings",
                        ["model_asset_id"])
        op.create_index("ix_model_bindings_target_type", "model_bindings", ["target_type"])

    if "model_versions" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_versions")}
        if "model_asset_id" not in cols:
            # SQLite 存量库无法安全地在 ALTER TABLE 中补外键约束，ORM 侧仍声明关系。
            op.add_column("model_versions", sa.Column("model_asset_id", sa.Integer(),
                                                       nullable=True))
            op.create_index("ix_model_versions_model_asset_id", "model_versions",
                            ["model_asset_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "model_versions" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_versions")}
        if "model_asset_id" in cols:
            op.drop_index("ix_model_versions_model_asset_id", table_name="model_versions")
            op.drop_column("model_versions", "model_asset_id")
    if "model_bindings" in tables:
        op.drop_table("model_bindings")
    if "model_assets" in tables:
        op.drop_table("model_assets")
