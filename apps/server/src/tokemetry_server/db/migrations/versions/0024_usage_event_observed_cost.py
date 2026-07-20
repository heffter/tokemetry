"""Add the usage_events_v2 observed_cost column (Task 65.6).

A proxy exporter may report the upstream cost it observed for an event. That
value is carried on the event row for drift reconciliation only -- Tokemetry
rate cards stay authoritative and the observed value never replaces computed
cost (D-016, FR-COST-003/004). The pricing path copies it onto the event's
``computed_costs.observed_cost`` row, which the reconciliation query reads. The
column is nullable and backfills to NULL (no observed cost known for prior
events).

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# Money type mirroring db/models.py's _MONEY (Numeric(20, 10)); declared locally
# so the migration does not import ORM code.
_MONEY = sa.Numeric(20, 10)


def upgrade() -> None:
    """Add the nullable ``usage_events_v2.observed_cost`` money column."""
    op.add_column(
        "usage_events_v2",
        sa.Column("observed_cost", _MONEY, nullable=True),
    )


def downgrade() -> None:
    """Drop the ``usage_events_v2.observed_cost`` column."""
    op.drop_column("usage_events_v2", "observed_cost")
