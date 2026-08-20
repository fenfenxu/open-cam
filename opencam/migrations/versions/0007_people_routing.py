"""员工、个人渠道、事件路由；events.verdict / assignee_id。

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "people" not in tables:
        op.create_table(
            "people",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("login_name", sa.String(64), nullable=True, unique=True),
            sa.Column("created_at", sa.Float(), nullable=False),
        )

    if "person_channels" not in tables:
        op.create_table(
            "person_channels",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("person_id", sa.Integer(),
                      sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("webhook", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        op.create_index("ix_person_channels_person_id", "person_channels", ["person_id"])

    if "event_routings" not in tables:
        op.create_table(
            "event_routings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("person_id", sa.Integer(),
                      sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
            sa.Column("camera_id", sa.Integer(), nullable=True),
            sa.Column("rule_type", sa.String(32), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        op.create_index("ix_event_routings_person_id", "event_routings", ["person_id"])

    if "events" in tables:
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "verdict" not in cols:
            op.add_column("events", sa.Column("verdict", sa.String(16), nullable=True))
        if "assignee_id" not in cols:
            # SQLite 不支持 ALTER TABLE 加外键约束；ORM 侧仍声明 ForeignKey。
            op.add_column("events", sa.Column("assignee_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "events" in tables:
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "assignee_id" in cols:
            op.drop_column("events", "assignee_id")
        if "verdict" in cols:
            op.drop_column("events", "verdict")
    if "event_routings" in tables:
        op.drop_table("event_routings")
    if "person_channels" in tables:
        op.drop_table("person_channels")
    if "people" in tables:
        op.drop_table("people")
