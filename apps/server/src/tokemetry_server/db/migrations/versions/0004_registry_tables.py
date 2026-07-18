"""provider, model, and model_alias registry tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere (mirrors 0001).
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Create the provider/model registry tables and their indexes."""
    op.create_table(
        "providers",
        sa.Column("id", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("aliases", _JSON, nullable=False),
        sa.Column("pricing_strategy", sa.String(50), nullable=False),
        sa.Column("limit_semantics", sa.String(50), nullable=False),
        sa.Column("supported_dimensions", _JSON, nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_providers"),
    )

    op.create_table(
        "models",
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("native_model_id", sa.String(200), nullable=False),
        sa.Column("lifecycle", sa.String(20), nullable=False),
        sa.Column("capabilities", _JSON, nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("provider", "native_model_id", name="pk_models"),
    )
    op.create_index("ix_models_last_seen", "models", ["last_seen"])

    op.create_table(
        "model_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("native_model_id", sa.String(200), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_aliases"),
        sa.UniqueConstraint("provider", "alias", name="model_aliases_grain"),
    )


def downgrade() -> None:
    """Drop the registry tables (reverse creation order)."""
    op.drop_table("model_aliases")
    op.drop_index("ix_models_last_seen", table_name="models")
    op.drop_table("models")
    op.drop_table("providers")
