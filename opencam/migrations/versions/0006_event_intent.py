"""规则意图与待办箱：rules.intent/escalate，events.intent/needs_action/repeat_count。

存量越线记为 observe + logged；其余告警 needs_action=1。幂等 ALTER。

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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "rules" in tables:
        cols = {c["name"] for c in inspector.get_columns("rules")}
        if "intent" not in cols:
            op.add_column("rules", sa.Column("intent", sa.String(16), nullable=True))
        if "escalate" not in cols:
            op.add_column("rules", sa.Column("escalate", sa.JSON(), nullable=True))
        op.execute(
            "UPDATE rules SET intent='observe' "
            "WHERE type='line_crossing' AND (intent IS NULL OR intent='')"
        )
        op.execute(
            "UPDATE rules SET intent='alert' WHERE intent IS NULL OR intent=''"
        )
        op.execute("UPDATE rules SET escalate='{}' WHERE escalate IS NULL")

    inspector = sa.inspect(bind)
    if "events" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("events")}
        if "intent" not in cols:
            op.add_column("events", sa.Column("intent", sa.String(16), nullable=True))
        if "needs_action" not in cols:
            op.add_column("events", sa.Column(
                "needs_action", sa.Boolean(), nullable=False,
                server_default=sa.false()))
            op.create_index("ix_events_needs_action", "events", ["needs_action"])
        if "repeat_count" not in cols:
            op.add_column("events", sa.Column(
                "repeat_count", sa.Integer(), nullable=False, server_default="1"))
        op.execute(
            "UPDATE events SET intent='observe', needs_action=0, status='logged' "
            "WHERE type='line_crossing'"
        )
        op.execute(
            "UPDATE events SET intent='alert', needs_action=1 "
            "WHERE type!='line_crossing' AND (intent IS NULL OR intent='')"
        )
        op.execute(
            "UPDATE events SET repeat_count=1 WHERE repeat_count IS NULL"
        )


def downgrade() -> None:
    op.drop_index("ix_events_needs_action", table_name="events")
    op.drop_column("events", "repeat_count")
    op.drop_column("events", "needs_action")
    op.drop_column("events", "intent")
    op.drop_column("rules", "escalate")
    op.drop_column("rules", "intent")
