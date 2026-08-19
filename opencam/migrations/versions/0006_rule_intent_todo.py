"""规则 intent/escalate 与事件待办字段。

存量越线回填为 observe + logged；其余告警回填 needs_action。
幂等：列已存在则跳过 ADD。

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


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    rule_cols = _cols("rules")
    event_cols = _cols("events")
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("events")}

    if "intent" not in rule_cols:
        op.add_column("rules", sa.Column("intent", sa.String(16), nullable=True))
    if "escalate" not in rule_cols:
        op.add_column("rules", sa.Column("escalate", sa.JSON(), nullable=True))
    if "intent" not in event_cols:
        op.add_column("events", sa.Column("intent", sa.String(16), nullable=True))
    if "needs_action" not in event_cols:
        op.add_column("events", sa.Column("needs_action", sa.Boolean(), nullable=True))
    if "repeat_count" not in event_cols:
        op.add_column(
            "events",
            sa.Column("repeat_count", sa.Integer(), nullable=True, server_default="1"),
        )

    op.execute(sa.text(
        "UPDATE rules SET intent='observe' "
        "WHERE type='line_crossing' AND (intent IS NULL OR intent='')"))
    op.execute(sa.text(
        "UPDATE rules SET intent='alert' WHERE intent IS NULL OR intent=''"))
    op.execute(sa.text(
        "UPDATE rules SET escalate='{}' WHERE escalate IS NULL"))

    op.execute(sa.text(
        "UPDATE events SET intent='observe', needs_action=0, status='logged' "
        "WHERE type='line_crossing'"))
    op.execute(sa.text(
        "UPDATE events SET intent='alert', needs_action=1 "
        "WHERE type!='line_crossing' AND (intent IS NULL OR intent='')"))
    op.execute(sa.text(
        "UPDATE events SET status='acked' "
        "WHERE type!='line_crossing' AND (status IS NULL OR status='') AND acked=1"))
    op.execute(sa.text(
        "UPDATE events SET status='open' "
        "WHERE type!='line_crossing' AND (status IS NULL OR status='')"))
    op.execute(sa.text(
        "UPDATE events SET needs_action=1 "
        "WHERE type!='line_crossing' AND needs_action IS NULL"))
    op.execute(sa.text(
        "UPDATE events SET repeat_count=1 WHERE repeat_count IS NULL"))

    if "ix_events_needs_action" not in indexes:
        op.create_index("ix_events_needs_action", "events", ["needs_action"])


def downgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("events")}
    if "ix_events_needs_action" in indexes:
        op.drop_index("ix_events_needs_action", table_name="events")
    event_cols = _cols("events")
    for name in ("repeat_count", "needs_action", "intent"):
        if name in event_cols:
            op.drop_column("events", name)
    rule_cols = _cols("rules")
    for name in ("escalate", "intent"):
        if name in rule_cols:
            op.drop_column("rules", name)
