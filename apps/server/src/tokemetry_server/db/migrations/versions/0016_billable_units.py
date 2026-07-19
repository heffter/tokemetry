"""billable_units table

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the billable_units table (non-token per-event quantities)."""
    op.create_table(
        "billable_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("unit_type", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_billable_units"),
        sa.UniqueConstraint(
            "provider", "event_id", "unit_type", name="billable_units_grain"
        ),
        sa.ForeignKeyConstraint(
            ["provider", "event_id"],
            ["usage_events_v2.provider", "usage_events_v2.event_id"],
            name="fk_billable_units_event",
        ),
    )


def downgrade() -> None:
    """Drop the billable_units table."""
    op.drop_table("billable_units")
