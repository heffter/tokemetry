"""Composite index for the final-attempt time-range scan (Task 66.8).

Every v2 read query (usage, costs, attempts, sessions) and the rollup refresh
filters ``usage_events_v2`` by ``event_kind='attempt' AND finality='final' AND
ts_started BETWEEN ...``. A composite index on those columns keeps the 30-day
aggregation and attempt-listing paths within the NFR-PERF-003 budget on the
reference dataset; the EXPLAIN review that surfaced it is recorded in
``docs/architecture/performance.md``.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_INDEX = "ix_usage_events_v2_attempt_ts"


def upgrade() -> None:
    """Create the (event_kind, finality, ts_started) index."""
    op.create_index(
        _INDEX, "usage_events_v2", ["event_kind", "finality", "ts_started"]
    )


def downgrade() -> None:
    """Drop the index."""
    op.drop_index(_INDEX, table_name="usage_events_v2")
