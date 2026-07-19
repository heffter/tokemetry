"""daily_rollups v2 grain, cost split, and Grafana compatibility view.

Evolves ``daily_rollups`` to the provider-neutral grain (FR-ROLLUP-004/005) and
splits cost by status (FR-ROLLUP-007, FR-COST-012), additively: the new grain
dimensions (``source``, ``environment``, ``billing_mode``) and cost columns are
added with sentinels/defaults, the unique grain is extended to include them plus
``provenance``, and the existing ``cost_usd`` is retained transitionally and
mirrored into ``cost_priced_usd``. The rollup service keeps writing the old shape
(with sentinel dimensions) until Task 66.2 reads ``usage_events_v2`` and
``computed_costs``. A stable ``daily_rollups_grafana`` view aggregates back to the
original grain so external dashboards keep a stable column set (FR-ROLLUP-011).

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

#: Cross-dialect money column: Numeric(20, 10).
_MONEY = sa.Numeric(20, 10)

#: The new provider-neutral grain (FR-ROLLUP-004/005).
_NEW_GRAIN = [
    "day", "provider", "model", "machine", "project",
    "source", "environment", "billing_mode", "provenance",
]

#: The original v1 grain, restored on downgrade.
_OLD_GRAIN = ["day", "provider", "machine", "model", "project"]

#: Columns added by this migration, dropped in reverse on downgrade.
_ADDED_COLUMNS = [
    "source", "environment", "billing_mode", "reasoning_tokens",
    "cost_priced_usd", "cost_partial_usd", "cost_estimated_usd",
    "unpriced_event_count", "subscription_value_usd",
]

#: A stable, original-grain view for external (Grafana) dashboards: it sums the
#: finer v2 grain back to (day, provider, model, machine, project) so existing
#: queries keep working regardless of the internal grain.
_GRAFANA_VIEW = """
CREATE VIEW daily_rollups_grafana AS
SELECT
    day, provider, model, machine, project,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cache_read_tokens) AS cache_read_tokens,
    SUM(cache_write_short_tokens) AS cache_write_short_tokens,
    SUM(cache_write_long_tokens) AS cache_write_long_tokens,
    SUM(reasoning_tokens) AS reasoning_tokens,
    SUM(total_tokens) AS total_tokens,
    SUM(cost_usd) AS cost_usd,
    SUM(cost_priced_usd) AS cost_priced_usd,
    SUM(cost_partial_usd) AS cost_partial_usd,
    SUM(cost_estimated_usd) AS cost_estimated_usd,
    SUM(unpriced_event_count) AS unpriced_event_count,
    SUM(subscription_value_usd) AS subscription_value_usd
FROM daily_rollups
GROUP BY day, provider, model, machine, project
"""


def upgrade() -> None:
    """Add the v2 grain columns and cost split; extend the grain; add the view."""
    with op.batch_alter_table("daily_rollups", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(200), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("environment", sa.String(50), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "billing_mode", sa.String(20), nullable=False,
                server_default="api_billed",
            )
        )
        batch_op.add_column(
            sa.Column(
                "reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(sa.Column("cost_priced_usd", _MONEY, nullable=True))
        batch_op.add_column(sa.Column("cost_partial_usd", _MONEY, nullable=True))
        batch_op.add_column(sa.Column("cost_estimated_usd", _MONEY, nullable=True))
        batch_op.add_column(
            sa.Column(
                "unpriced_event_count", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(sa.Column("subscription_value_usd", _MONEY, nullable=True))
        batch_op.drop_constraint("daily_rollups_grain", type_="unique")
        batch_op.create_unique_constraint("daily_rollups_grain", _NEW_GRAIN)

    # Existing rollup cost was the priced v1 keep-max cost: seed the priced bucket.
    op.execute("UPDATE daily_rollups SET cost_priced_usd = cost_usd WHERE cost_usd IS NOT NULL")
    op.execute("DROP VIEW IF EXISTS daily_rollups_grafana")
    op.execute(_GRAFANA_VIEW)


def downgrade() -> None:
    """Drop the view, restore the v1 grain, and drop the added columns."""
    op.execute("DROP VIEW IF EXISTS daily_rollups_grafana")
    with op.batch_alter_table("daily_rollups", schema=None) as batch_op:
        batch_op.drop_constraint("daily_rollups_grain", type_="unique")
        batch_op.create_unique_constraint("daily_rollups_grain", _OLD_GRAIN)
        for name in reversed(_ADDED_COLUMNS):
            batch_op.drop_column(name)
