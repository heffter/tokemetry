"""attribute backfilled v1 rows to derived collector sources

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from tokemetry_server.db.backfill import (
    attribute_backfilled_sources,
    remove_collector_source_attribution,
)

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Attribute source-less backfilled v1 rows to per-machine collector sources."""
    attribute_backfilled_sources(op.get_bind())


def downgrade() -> None:
    """Unlink and remove the derived collector sources."""
    remove_collector_source_attribution(op.get_bind())
