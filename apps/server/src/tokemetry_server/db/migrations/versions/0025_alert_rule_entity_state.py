"""Add the alert_rules.entity_state column for per-entity firing state (Task 68.2).

Rule kinds that fire one alert per entity -- ``stale_source`` fires one alert
per reporting source -- need per-entity firing state so multiple entities notify
and resolve independently (FR-ALERT-003, FR-SOURCE-007). Single-finding rules
leave the column NULL and continue to use ``state``/``last_fired_at``. The
column is nullable and backfills to NULL (existing rules keep their behavior).

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Cross-dialect JSON (JSONB on Postgres), mirroring db/base.py's JSONType.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add the nullable ``alert_rules.entity_state`` JSON column."""
    op.add_column(
        "alert_rules",
        sa.Column("entity_state", _JSON, nullable=True),
    )


def downgrade() -> None:
    """Drop the ``alert_rules.entity_state`` column."""
    op.drop_column("alert_rules", "entity_state")
