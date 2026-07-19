"""Promote the v2 limit-snapshot dimensions to columns (Task 69.2).

The v2 limit dimensions (account, organization, source, limit_amount,
remaining, unit) were stashed in ``limit_snapshots.raw`` transitionally; this
migration gives them first-class nullable columns (FR-LIMIT-002/003) so limit
streams can be keyed and grouped by them (FR-LIMIT-005). All columns are
nullable, so existing rows -- and v1 dimension-less snapshots -- migrate cleanly
with null new dimensions. ``window_kind`` stays opaque (FR-LIMIT-001).

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_COLUMN_NAMES = (
    "account",
    "organization",
    "source_id",
    "limit_amount",
    "remaining",
    "unit",
)


def upgrade() -> None:
    """Add the v2 dimension/measure columns to limit_snapshots."""
    op.add_column("limit_snapshots", sa.Column("account", sa.String(200), nullable=True))
    op.add_column(
        "limit_snapshots", sa.Column("organization", sa.String(200), nullable=True)
    )
    op.add_column("limit_snapshots", sa.Column("source_id", sa.Integer(), nullable=True))
    op.add_column(
        "limit_snapshots", sa.Column("limit_amount", sa.Numeric(20, 4), nullable=True)
    )
    op.add_column(
        "limit_snapshots", sa.Column("remaining", sa.Numeric(20, 4), nullable=True)
    )
    op.add_column("limit_snapshots", sa.Column("unit", sa.String(30), nullable=True))
    op.create_index(
        "ix_limit_snapshots_source_id", "limit_snapshots", ["source_id"]
    )


def downgrade() -> None:
    """Drop the v2 dimension/measure columns."""
    op.drop_index("ix_limit_snapshots_source_id", table_name="limit_snapshots")
    for name in reversed(_COLUMN_NAMES):
        op.drop_column("limit_snapshots", name)
