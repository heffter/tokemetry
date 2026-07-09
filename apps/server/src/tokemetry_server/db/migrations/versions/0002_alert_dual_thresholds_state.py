"""alert dual thresholds and firing state

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_THRESH = sa.Numeric(12, 4)


def upgrade() -> None:
    """Add warn/crit thresholds and the firing-state columns to alert_rules."""
    op.add_column("alert_rules", sa.Column("warn_threshold", _THRESH, nullable=True))
    op.add_column("alert_rules", sa.Column("crit_threshold", _THRESH, nullable=True))
    op.add_column(
        "alert_rules",
        sa.Column("state", sa.String(20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "alert_rules",
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the columns added in this revision."""
    op.drop_column("alert_rules", "last_fired_at")
    op.drop_column("alert_rules", "state")
    op.drop_column("alert_rules", "crit_threshold")
    op.drop_column("alert_rules", "warn_threshold")
