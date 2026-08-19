"""events.source_offset：文件源事件在素材中的播放位置（秒）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "events" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("events")}
    if "source_offset" not in cols:
        op.add_column("events", sa.Column("source_offset", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "source_offset")
