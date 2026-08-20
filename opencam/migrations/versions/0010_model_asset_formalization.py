"""模型资产正式化：来源/交付拆分、能力契约、版本哈希与框架运行时。

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-20

原型单一 source_type 拆为 origin_type（产生方式）+ distribution_type（交付方式），
旧列保留一个版本（双写过渡），回填映射：
    builtin   → (builtin, private)
    uploaded  → (uploaded, private)
    trained   → (trained, private)
    published → (uploaded, published)
    solution  → (builtin, solution)
model_versions 补齐 artifact_hash / framework / runtime / input_size，
已存在产物文件就地补算 sha256；文件缺失则留 NULL，不阻断升级。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGIN_BACKFILL = {
    "builtin": ("builtin", "private"),
    "uploaded": ("uploaded", "private"),
    "trained": ("trained", "private"),
    "published": ("uploaded", "published"),
    "solution": ("builtin", "solution"),
}


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "model_assets" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_assets")}
        if "origin_type" not in cols:
            op.add_column("model_assets", sa.Column(
                "origin_type", sa.String(24), nullable=False, server_default=""))
            op.create_index("ix_model_assets_origin_type",
                            "model_assets", ["origin_type"])
        if "distribution_type" not in cols:
            op.add_column("model_assets", sa.Column(
                "distribution_type", sa.String(24), nullable=False,
                server_default="private"))
            op.create_index("ix_model_assets_distribution_type",
                            "model_assets", ["distribution_type"])
        if "capabilities" not in cols:
            op.add_column("model_assets", sa.Column(
                "capabilities", sa.JSON(), nullable=False,
                server_default=sa.text("'[]'")))
        if "input_contract" not in cols:
            op.add_column("model_assets", sa.Column(
                "input_contract", sa.JSON(), nullable=False,
                server_default=sa.text("'{}'")))
        if "output_contract" not in cols:
            op.add_column("model_assets", sa.Column(
                "output_contract", sa.JSON(), nullable=False,
                server_default=sa.text("'{}'")))

        bind = op.get_bind()
        for old, (origin, distribution) in _ORIGIN_BACKFILL.items():
            bind.execute(
                sa.text(
                    "UPDATE model_assets SET origin_type = :origin, "
                    "distribution_type = :distribution "
                    "WHERE source_type = :old AND origin_type = ''"),
                {"origin": origin, "distribution": distribution, "old": old},
            )

    if "model_versions" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_versions")}
        if "artifact_hash" not in cols:
            op.add_column("model_versions", sa.Column(
                "artifact_hash", sa.String(64), nullable=True))
        if "framework" not in cols:
            op.add_column("model_versions", sa.Column(
                "framework", sa.String(32), nullable=True))
        if "runtime" not in cols:
            op.add_column("model_versions", sa.Column(
                "runtime", sa.String(32), nullable=True))
        if "input_size" not in cols:
            op.add_column("model_versions", sa.Column(
                "input_size", sa.Integer(), nullable=True))

        bind = op.get_bind()
        rows = bind.execute(
            sa.text("SELECT id, artifact_path FROM model_versions "
                    "WHERE artifact_hash IS NULL")).fetchall()
        for row_id, artifact_path in rows:
            if not artifact_path:
                continue
            digest = _sha256(Path(artifact_path))
            if digest is None:
                continue
            bind.execute(
                sa.text("UPDATE model_versions SET artifact_hash = :digest "
                        "WHERE id = :row_id"),
                {"digest": digest, "row_id": row_id},
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "model_versions" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_versions")}
        for col in ("input_size", "runtime", "framework", "artifact_hash"):
            if col in cols:
                op.drop_column("model_versions", col)
    if "model_assets" in tables:
        cols = {c["name"] for c in inspector.get_columns("model_assets")}
        for col in ("output_contract", "input_contract", "capabilities"):
            if col in cols:
                op.drop_column("model_assets", col)
        if "distribution_type" in cols:
            op.drop_index("ix_model_assets_distribution_type",
                          table_name="model_assets")
            op.drop_column("model_assets", "distribution_type")
        if "origin_type" in cols:
            op.drop_index("ix_model_assets_origin_type", table_name="model_assets")
            op.drop_column("model_assets", "origin_type")
