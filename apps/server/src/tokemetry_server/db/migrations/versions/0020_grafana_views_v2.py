"""Versioned Grafana-compatible SQL views (FR-QUERY-014, FR-ROLLUP-011).

Stable, standard-SQL views for external dashboards, usable identically on SQLite
and Postgres. ``grafana_daily_usage_v2`` and ``grafana_costs_v2`` aggregate
``daily_rollups`` back to the classic ``(day, provider, model, machine,
project)`` grain (so the finer v2 grain is invisible to consumers, FR-ROLLUP-011)
and split usage from the dual cost metrics; ``grafana_limits_v2`` projects the
limit snapshots. View schema changes must ship as a documented migration.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_VIEWS = ("grafana_daily_usage_v2", "grafana_costs_v2", "grafana_limits_v2")

_DAILY_USAGE = """
CREATE VIEW grafana_daily_usage_v2 AS
SELECT
    day, provider, model, machine, project,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cache_read_tokens) AS cache_read_tokens,
    SUM(cache_write_short_tokens) AS cache_write_short_tokens,
    SUM(cache_write_long_tokens) AS cache_write_long_tokens,
    SUM(reasoning_tokens) AS reasoning_tokens,
    SUM(total_tokens) AS total_tokens
FROM daily_rollups
GROUP BY day, provider, model, machine, project
"""

_COSTS = """
CREATE VIEW grafana_costs_v2 AS
SELECT
    day, provider, model, machine, project,
    SUM(cost_priced_usd) AS cost_priced_usd,
    SUM(cost_partial_usd) AS cost_partial_usd,
    SUM(cost_estimated_usd) AS cost_estimated_usd,
    SUM(subscription_value_usd) AS subscription_value_usd,
    SUM(unpriced_event_count) AS unpriced_event_count
FROM daily_rollups
GROUP BY day, provider, model, machine, project
"""

_LIMITS = """
CREATE VIEW grafana_limits_v2 AS
SELECT ts, provider, machine, window_kind, utilization_pct, provenance, resets_at
FROM limit_snapshots
"""


def upgrade() -> None:
    """Create the versioned Grafana views."""
    for statement in (_DAILY_USAGE, _COSTS, _LIMITS):
        op.execute(statement)


def downgrade() -> None:
    """Drop the Grafana views."""
    for view in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
