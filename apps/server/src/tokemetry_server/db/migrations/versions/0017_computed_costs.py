"""computed_costs table and v1 cost materialization

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from tokemetry_server.db.pricing_migration import materialize_computed_costs

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Precision for money columns: 20 digits, 10 after the decimal point.
_MONEY = sa.Numeric(20, 10)
#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Create computed_costs and materialize the transitional v1 cost into it."""
    op.create_table(
        "computed_costs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("pricing_version", sa.String(50), nullable=False),
        sa.Column("cost_status", sa.String(20), nullable=False),
        sa.Column("amount", _MONEY, nullable=True),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("billing_mode", sa.String(20), nullable=False),
        sa.Column("subscription_equivalent_amount", _MONEY, nullable=True),
        sa.Column("missing_units", _JSON, nullable=True),
        sa.Column("observed_cost", _MONEY, nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_computed_costs"),
        sa.UniqueConstraint(
            "provider", "event_id", "pricing_version", name="computed_costs_grain"
        ),
        sa.ForeignKeyConstraint(
            ["provider", "event_id"],
            ["usage_events_v2.provider", "usage_events_v2.event_id"],
            name="fk_computed_costs_event",
        ),
    )
    op.create_index(
        "ix_computed_costs_event", "computed_costs", ["provider", "event_id"]
    )
    materialize_computed_costs(op.get_bind())


def downgrade() -> None:
    """Drop the computed_costs table."""
    op.drop_index("ix_computed_costs_event", table_name="computed_costs")
    op.drop_table("computed_costs")
