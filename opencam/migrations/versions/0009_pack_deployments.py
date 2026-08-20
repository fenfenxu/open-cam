"""方案包部署记录与资源映射。

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "pack_deployments" not in tables:
        op.create_table(
            "pack_deployments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("pack_id", sa.String(128), nullable=False),
            sa.Column("pack_version", sa.String(64), nullable=False),
            sa.Column("pack_digest", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False,
                      server_default="configuring"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_pack_deployments_pack_id", "pack_deployments",
                        ["pack_id"])
        op.create_index("ix_pack_deployments_status", "pack_deployments",
                        ["status"])

    if "pack_deployment_resources" not in tables:
        op.create_table(
            "pack_deployment_resources",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("deployment_id", sa.Integer(), nullable=False),
            sa.Column("camera_slot_id", sa.String(64), nullable=False,
                      server_default="default"),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("resource_id", sa.Integer(), nullable=False),
            sa.Column("ownership", sa.String(16), nullable=False,
                      server_default="created"),
            sa.Column("configured", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.ForeignKeyConstraint(
                ["deployment_id"], ["pack_deployments.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_pack_deployment_resources_deployment_id",
            "pack_deployment_resources",
            ["deployment_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "pack_deployment_resources" in tables:
        op.drop_table("pack_deployment_resources")
    if "pack_deployments" in tables:
        op.drop_table("pack_deployments")
