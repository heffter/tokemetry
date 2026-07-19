"""Add the provider window registry column (Task 69.1).

Providers gain a ``windows`` JSON column describing their limit-window kinds --
each with a display label and period semantics -- so dashboards and alerts
resolve window labels from the registry instead of hardcoding ``five_hour`` /
``seven_day`` (FR-LIMIT-012). Window kinds stay opaque in storage (FR-LIMIT-001)
and unknown kinds never require a migration (FR-LIMIT-009): they simply have no
descriptor and fall back to the raw kind. The column defaults to an empty list,
so existing providers backfill to "no declared windows" and the Anthropic seed
repopulates its windows on the next startup seed.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# Cross-dialect JSON (JSONB on Postgres), mirroring db/base.py's JSONType. The
# migration declares it locally rather than importing ORM code.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add the ``providers.windows`` JSON column, defaulting to an empty list."""
    op.add_column(
        "providers",
        sa.Column("windows", _JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    """Drop the ``providers.windows`` column."""
    op.drop_column("providers", "windows")
