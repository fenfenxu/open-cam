"""基线：等价于 0.2.x 的 schema（cameras / rules / events；rules 含 name 列，events 含 source_offset 列）。

全新库由 Base.metadata.create_all 直接建表后 stamp 到 head；
≤0.2.0 的存量库经旧补丁后 stamp 到本版本。因此本脚本不含任何 DDL，
它只是版本链的起点。后续 schema 变更请新增版本脚本（见 AGENTS.md）。

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""

from __future__ import annotations

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
