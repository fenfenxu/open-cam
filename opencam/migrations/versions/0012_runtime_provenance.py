"""运行时模型解析与事件产物留痕。

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("events")}
    additions = (
        ("analysis_profile_version", sa.String(64)),
        ("pipeline_stage", sa.String(128)),
        ("model_version_id", sa.Integer()),
        ("artifact_digest", sa.String(64)),
    )
    for name, column_type in additions:
        if name in columns:
            continue
        op.add_column("events", sa.Column(name, column_type, nullable=True))
    # SQLite 旧库也能安全使用这条迁移；外键约束由应用层的 SET NULL 语义兜底。


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("events")}
    for name in ("artifact_digest", "model_version_id", "pipeline_stage",
                 "analysis_profile_version"):
        if name in columns:
            op.drop_column("events", name)
