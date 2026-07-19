"""rate_cards table and lossless pricing expansion

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from tokemetry_server.db.pricing_migration import expand_pricing_to_rate_cards

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Precision for money columns: 20 digits, 10 after the decimal point.
_MONEY = sa.Numeric(20, 10)


def upgrade() -> None:
    """Create rate_cards and expand existing pricing rows into it (lossless)."""
    op.create_table(
        "rate_cards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("native_model", sa.String(200), nullable=False),
        sa.Column("unit_type", sa.String(50), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("region", sa.String(50), nullable=True),
        sa.Column("service_tier", sa.String(50), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("context_bracket", sa.String(50), nullable=True),
        sa.Column("unit_price", _MONEY, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("override", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rate_cards"),
        sa.UniqueConstraint(
            "provider",
            "native_model",
            "unit_type",
            "effective_from",
            "service_tier",
            "mode",
            "context_bracket",
            "priority",
            name="rate_cards_grain",
        ),
    )
    op.create_index(
        "ix_rate_cards_lookup",
        "rate_cards",
        ["provider", "native_model", "unit_type", "effective_from"],
    )
    expand_pricing_to_rate_cards(op.get_bind())


def downgrade() -> None:
    """Drop rate_cards; the v1 pricing table is untouched by this migration."""
    op.drop_index("ix_rate_cards_lookup", table_name="rate_cards")
    op.drop_table("rate_cards")
