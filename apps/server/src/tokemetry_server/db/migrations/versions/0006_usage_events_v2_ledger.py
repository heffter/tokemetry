"""usage_events_v2, usage_event_revisions, and logical_requests ledger

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere (mirrors 0001).
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

#: Single-column indexes on ``usage_events_v2``. Names follow the ORM naming
#: convention (``ix_<table>_<column>``) so migrated and ORM schemas agree.
_USAGE_V2_INDEXES = (
    "logical_request_id",
    "provider_request_id",
    "native_model",
    "ts_started",
    "machine",
    "session_id",
    "outcome",
    "source_id",
    "trace_id",
    "span_id",
    "parent_span_id",
)


def upgrade() -> None:
    """Create the v2 ledger, revision archive, and logical-request tables."""
    op.create_table(
        "usage_events_v2",
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_kind", sa.String(30), nullable=False),
        sa.Column("finality", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("logical_request_id", sa.String(200), nullable=True),
        sa.Column("attempt_id", sa.String(200), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("provider_response_id", sa.String(200), nullable=True),
        sa.Column("requested_model", sa.String(200), nullable=True),
        sa.Column("routed_model", sa.String(200), nullable=True),
        sa.Column("native_model", sa.String(200), nullable=False),
        sa.Column("ts_started", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_first_token", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts_completed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("machine", sa.String(200), nullable=True),
        sa.Column("project", sa.String(500), nullable=True),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("agent_id", sa.String(200), nullable=True),
        sa.Column("environment", sa.String(50), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_short_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_long_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.String(50), nullable=True),
        sa.Column("service_tier", sa.String(50), nullable=True),
        sa.Column("streaming", sa.Boolean(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("time_to_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("tool_histogram", _JSON, nullable=True),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("routing", _JSON, nullable=True),
        sa.Column("dimensions", _JSON, nullable=False),
        sa.Column("extra", _JSON, nullable=False),
        sa.Column("trace_id", sa.String(200), nullable=True),
        sa.Column("span_id", sa.String(200), nullable=True),
        sa.Column("parent_span_id", sa.String(200), nullable=True),
        sa.PrimaryKeyConstraint("provider", "event_id", name="pk_usage_events_v2"),
    )
    for column in _USAGE_V2_INDEXES:
        op.create_index(
            f"ix_usage_events_v2_{column}", "usage_events_v2", [column]
        )

    op.create_table(
        "usage_event_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("finality", sa.String(20), nullable=False),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(200), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usage_event_revisions"),
    )
    op.create_index(
        "ix_usage_event_revisions_provider_event",
        "usage_event_revisions",
        ["provider", "event_id"],
    )

    op.create_table(
        "logical_requests",
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("logical_request_id", sa.String(200), nullable=False),
        sa.Column("requested_model", sa.String(200), nullable=True),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("routing_policy", sa.String(100), nullable=True),
        sa.Column("routing_reason", sa.String(100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("fallback_count", sa.Integer(), nullable=False),
        sa.Column("winning_attempt_id", sa.String(200), nullable=True),
        sa.Column("ts_first", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts_last", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "provider", "logical_request_id", name="pk_logical_requests"
        ),
    )


def downgrade() -> None:
    """Drop the v2 ledger tables and their indexes (reverse creation order)."""
    op.drop_table("logical_requests")
    op.drop_index(
        "ix_usage_event_revisions_provider_event",
        table_name="usage_event_revisions",
    )
    op.drop_table("usage_event_revisions")
    for column in reversed(_USAGE_V2_INDEXES):
        op.drop_index(f"ix_usage_events_v2_{column}", table_name="usage_events_v2")
    op.drop_table("usage_events_v2")
