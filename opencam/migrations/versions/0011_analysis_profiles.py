"""分析方案、推理阶段、摄像头绑定与能力化规则。

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "analysis_profiles" not in tables:
        op.create_table(
            "analysis_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(128), nullable=False, unique=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.String(64), nullable=False, server_default="1"),
            sa.Column("input_contract", sa.JSON(), nullable=False,
                      server_default=sa.text("'{}'")),
            sa.Column("frame_rate", sa.Float(), nullable=True),
            sa.Column("latency_budget_ms", sa.Float(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("solution_pack_id", sa.String(128), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False,
                      server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_analysis_profiles_key", "analysis_profiles", ["key"])
        op.create_index("ix_analysis_profiles_status", "analysis_profiles", ["status"])

    if "pipeline_stages" not in tables:
        op.create_table(
            "pipeline_stages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("profile_id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("capabilities", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
            sa.Column("input_contract", sa.JSON(), nullable=False,
                      server_default=sa.text("'{}'")),
            sa.Column("output_contract", sa.JSON(), nullable=False,
                      server_default=sa.text("'{}'")),
            sa.Column("model_slot_key", sa.String(128), nullable=True),
            sa.Column("model_version_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(
                ["profile_id"], ["analysis_profiles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["model_version_id"], ["model_versions.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_pipeline_stages_profile_id", "pipeline_stages", ["profile_id"])

    if "camera_bindings" not in tables:
        op.create_table(
            "camera_bindings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("camera_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("analysis_profile_id", sa.Integer(), nullable=False),
            sa.Column("profile_version", sa.String(64), nullable=False,
                      server_default="1"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(
                ["camera_id"], ["cameras.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["analysis_profile_id"], ["analysis_profiles.id"], ondelete="RESTRICT"),
        )
        op.create_index("ix_camera_bindings_camera_id", "camera_bindings", ["camera_id"])
        op.create_index(
            "ix_camera_bindings_analysis_profile_id",
            "camera_bindings", ["analysis_profile_id"])

    rule_cols = {c["name"] for c in inspector.get_columns("rules")}
    if "capabilities" not in rule_cols:
        op.add_column(
            "rules", sa.Column("capabilities", sa.JSON(), nullable=False,
                                server_default=sa.text("'[]'")))

    binding_cols = {c["name"] for c in inspector.get_columns("model_bindings")}
    if "relation_status" not in binding_cols:
        op.add_column(
            "model_bindings",
            sa.Column("relation_status", sa.String(16), nullable=False,
                      server_default="confirmed"))
        op.create_index(
            "ix_model_bindings_relation_status", "model_bindings", ["relation_status"])
        bind.execute(
            sa.text(
                "UPDATE model_bindings SET relation_status = 'pending', enabled = 0 "
                "WHERE relation_source = 'ai_recommended'"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "model_bindings" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_bindings")}
        if "relation_status" in cols:
            op.drop_index("ix_model_bindings_relation_status", table_name="model_bindings")
            op.drop_column("model_bindings", "relation_status")
    if "rules" in tables:
        cols = {c["name"] for c in inspector.get_columns("rules")}
        if "capabilities" in cols:
            op.drop_column("rules", "capabilities")
    if "camera_bindings" in tables:
        op.drop_table("camera_bindings")
    if "pipeline_stages" in tables:
        op.drop_table("pipeline_stages")
    if "analysis_profiles" in tables:
        op.drop_index("ix_analysis_profiles_status", table_name="analysis_profiles")
        op.drop_index("ix_analysis_profiles_key", table_name="analysis_profiles")
        op.drop_table("analysis_profiles")
