"""规则意图与待办旗标：rules.intent/escalate，events.intent/needs_action/repeat_count。

存量越线回填为观察记录；其余保持待办。幂等：列已存在则跳过。

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "rules" in tables:
        cols = {c["name"] for c in inspector.get_columns("rules")}
        if "intent" not in cols:
            op.add_column("rules", sa.Column(
                "intent", sa.String(16), nullable=False, server_default="alert"))
        if "escalate" not in cols:
            op.add_column("rules", sa.Column(
                "escalate", sa.JSON(), nullable=False, server_default="{}"))
        op.execute("UPDATE rules SET escalate='{}' WHERE escalate IS NULL")
        op.execute("UPDATE rules SET intent='observe' WHERE type='line_crossing'")
        op.execute("UPDATE rules SET intent='alert' "
                   "WHERE intent IS NULL OR intent=''")

    if "events" in tables:
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "intent" not in cols:
            op.add_column("events", sa.Column(
                "intent", sa.String(16), nullable=False, server_default="alert"))
        if "needs_action" not in cols:
            op.add_column("events", sa.Column(
                "needs_action", sa.Boolean(), nullable=False,
                server_default=sa.true()))
            op.create_index("ix_events_needs_action", "events", ["needs_action"])
        if "repeat_count" not in cols:
            op.add_column("events", sa.Column(
                "repeat_count", sa.Integer(), nullable=False, server_default="1"))
        op.execute(
            "UPDATE events SET intent='observe', needs_action=0, status='logged' "
            "WHERE type='line_crossing'")
        op.execute(
            "UPDATE events SET intent='alert', needs_action=1 "
            "WHERE type!='line_crossing'")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "events" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "repeat_count" in cols:
            op.drop_column("events", "repeat_count")
        if "needs_action" in cols:
            op.drop_index("ix_events_needs_action", table_name="events")
            op.drop_column("events", "needs_action")
        if "intent" in cols:
            op.drop_column("events", "intent")
    if "rules" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("rules")}
        if "escalate" in cols:
            op.drop_column("rules", "escalate")
        if "intent" in cols:
            op.drop_column("rules", "intent")
