"""事件处置闭环：events 增加 status/starred/assignee/note，新建 event_actions 与 notify_channels。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "events" in tables:
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "status" not in cols:
            op.add_column("events", sa.Column(
                "status", sa.String(16), nullable=False, server_default="open"))
            op.create_index("ix_events_status", "events", ["status"])
        if "starred" not in cols:
            op.add_column("events", sa.Column(
                "starred", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "assignee" not in cols:
            op.add_column("events", sa.Column("assignee", sa.String(64), nullable=True))
        if "note" not in cols:
            op.add_column("events", sa.Column("note", sa.Text(), nullable=True))
        # 历史数据回填：已 ack 的事件状态置为 acked
        op.execute("UPDATE events SET status='acked' WHERE acked=1 AND status='open'")

    if "event_actions" not in tables:
        op.create_table(
            "event_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.Integer(),
                      sa.ForeignKey("events.id"), nullable=False),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("actor", sa.String(64), nullable=False, server_default="local"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("ts", sa.Float(), nullable=False),
        )
        op.create_index("ix_event_actions_event_id", "event_actions", ["event_id"])
        op.create_index("ix_event_actions_ts", "event_actions", ["ts"])

    if "notify_channels" not in tables:
        op.create_table(
            "notify_channels",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("webhook", sa.Text(), nullable=False),
            sa.Column("camera_id", sa.Integer(), nullable=True),
            sa.Column("rule_type", sa.String(32), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_table("notify_channels")
    op.drop_table("event_actions")
    op.drop_column("events", "note")
    op.drop_column("events", "assignee")
    op.drop_column("events", "starred")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_column("events", "status")
