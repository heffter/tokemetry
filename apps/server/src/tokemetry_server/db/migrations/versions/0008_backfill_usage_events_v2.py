"""backfill usage_events into usage_events_v2

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from tokemetry_server.db.backfill import (
    backfill_usage_events_v2,
    remove_backfilled_rows,
)

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Copy every v1 usage_events row into the v2 ledger (idempotent)."""
    backfill_usage_events_v2(op.get_bind())


def downgrade() -> None:
    """Remove only the rows this backfill created (by their marker)."""
    remove_backfilled_rows(op.get_bind())
