"""transitional cost_usd column on usage_events_v2

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from tokemetry_server.db.backfill import populate_transitional_cost

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Precision for money columns: 20 digits, 10 after the decimal point.
_MONEY = sa.Numeric(20, 10)


def upgrade() -> None:
    """Add the transitional cost column and populate it for backfilled rows."""
    op.add_column("usage_events_v2", sa.Column("cost_usd", _MONEY, nullable=True))
    populate_transitional_cost(op.get_bind())


def downgrade() -> None:
    """Drop the transitional cost column."""
    op.drop_column("usage_events_v2", "cost_usd")
