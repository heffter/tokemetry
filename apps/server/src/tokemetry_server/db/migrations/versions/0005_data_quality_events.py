"""data_quality_events table

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere (mirrors 0001).
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Create the data-quality anomaly sink table and its indexes."""
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("detail", _JSON, nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_data_quality_events"),
    )
    op.create_index("ix_data_quality_events_kind", "data_quality_events", ["kind"])
    op.create_index("ix_data_quality_events_ts", "data_quality_events", ["ts"])


def downgrade() -> None:
    """Drop the data-quality table and its indexes."""
    op.drop_index("ix_data_quality_events_ts", table_name="data_quality_events")
    op.drop_index("ix_data_quality_events_kind", table_name="data_quality_events")
    op.drop_table("data_quality_events")
