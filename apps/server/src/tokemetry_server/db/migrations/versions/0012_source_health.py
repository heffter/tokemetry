"""source health-tracking columns

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-19
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
    """Add per-source health-tracking columns."""
    op.add_column(
        "sources",
        sa.Column("last_successful_ingest", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "recent_error_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "sources",
        sa.Column("error_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources", sa.Column("reported_schema_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "sources", sa.Column("clock_skew_seconds", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    """Drop the health-tracking columns."""
    for name in (
        "clock_skew_seconds",
        "reported_schema_version",
        "error_window_started_at",
        "recent_error_count",
        "last_successful_ingest",
    ):
        op.drop_column("sources", name)
