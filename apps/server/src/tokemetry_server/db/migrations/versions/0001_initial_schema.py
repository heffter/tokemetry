"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

#: Money precision matching the ORM (20 digits, 10 fractional).
_MONEY = sa.Numeric(20, 10)


def upgrade() -> None:
    """Create all tables."""
    op.create_table(
        "machines",
        sa.Column("id", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collector_version", sa.String(50), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machines"),
    )

    op.create_table(
        "usage_events",
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("machine", sa.String(200), nullable=True),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("project", sa.String(500), nullable=True),
        sa.Column("git_branch", sa.String(300), nullable=True),
        sa.Column("client_version", sa.String(50), nullable=True),
        sa.Column("entrypoint", sa.String(50), nullable=True),
        sa.Column("is_sidechain", sa.Boolean(), nullable=False),
        sa.Column("session_kind", sa.String(50), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_short_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_long_tokens", sa.BigInteger(), nullable=False),
        sa.Column("service_tier", sa.String(50), nullable=True),
        sa.Column("speed", sa.String(50), nullable=True),
        sa.Column("cost_usd", _MONEY, nullable=True),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("extra", _JSON, nullable=False),
        sa.PrimaryKeyConstraint("provider", "event_id", name="pk_usage_events"),
    )
    op.create_index("ix_usage_events_machine", "usage_events", ["machine"])
    op.create_index("ix_usage_events_session_id", "usage_events", ["session_id"])
    op.create_index("ix_usage_events_ts", "usage_events", ["ts"])
    op.create_index("ix_usage_events_model", "usage_events", ["model"])

    op.create_table(
        "limit_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("machine", sa.String(200), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_kind", sa.String(50), nullable=False),
        sa.Column("utilization_pct", sa.Numeric(7, 3), nullable=False),
        sa.Column("resets_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("raw", _JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_limit_snapshots"),
    )
    op.create_index("ix_limit_snapshots_provider", "limit_snapshots", ["provider"])
    op.create_index("ix_limit_snapshots_machine", "limit_snapshots", ["machine"])
    op.create_index("ix_limit_snapshots_ts", "limit_snapshots", ["ts"])
    op.create_index("ix_limit_snapshots_window_kind", "limit_snapshots", ["window_kind"])

    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("machine", sa.String(200), nullable=True),
        sa.Column("project", sa.String(500), nullable=True),
        sa.Column("slug", sa.String(300), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_short_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_long_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", _MONEY, nullable=True),
        sa.PrimaryKeyConstraint("session_id", name="pk_sessions"),
    )
    op.create_index("ix_sessions_provider", "sessions", ["provider"])
    op.create_index("ix_sessions_machine", "sessions", ["machine"])
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"])

    op.create_table(
        "daily_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("machine", sa.String(200), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("project", sa.String(500), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_short_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_long_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", _MONEY, nullable=True),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_rollups"),
        sa.UniqueConstraint(
            "day", "provider", "machine", "model", "project", name="daily_rollups_grain"
        ),
    )
    op.create_index("ix_daily_rollups_day", "daily_rollups", ["day"])

    op.create_table(
        "pricing",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("input_per_mtok", _MONEY, nullable=False),
        sa.Column("output_per_mtok", _MONEY, nullable=False),
        sa.Column("cache_read_per_mtok", _MONEY, nullable=False),
        sa.Column("cache_write_short_per_mtok", _MONEY, nullable=False),
        sa.Column("cache_write_long_per_mtok", _MONEY, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pricing"),
        sa.UniqueConstraint("provider", "model", "effective_date", name="pricing_grain"),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 4), nullable=True),
        sa.Column("window_kind", sa.String(50), nullable=True),
        sa.Column("channels", _JSON, nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("quiet_hours", _JSON, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", _JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_alert_rules"),
        sa.UniqueConstraint("name", name="uq_alert_rules_name"),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("context", _JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_alert_events"),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["alert_rules.id"], name="fk_alert_events_rule_id_alert_rules"
        ),
    )
    op.create_index("ix_alert_events_rule_id", "alert_events", ["rule_id"])
    op.create_index("ix_alert_events_ts", "alert_events", ["ts"])

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_api_tokens"),
        sa.UniqueConstraint("label", name="uq_api_tokens_label"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("api_tokens")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
    op.drop_table("pricing")
    op.drop_table("daily_rollups")
    op.drop_table("sessions")
    op.drop_table("limit_snapshots")
    op.drop_table("usage_events")
    op.drop_table("machines")
