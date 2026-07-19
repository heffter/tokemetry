"""api_tokens scopes and source allowlist

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from tokemetry_server.scopes import ALL_SCOPES

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Cross-dialect JSON column: JSONB on Postgres, JSON elsewhere (mirrors 0001).
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add scopes and source_allowlist; grant existing tokens all scopes."""
    op.add_column("api_tokens", sa.Column("scopes", _JSON, nullable=True))
    op.add_column("api_tokens", sa.Column("source_allowlist", _JSON, nullable=True))

    # Existing tokens keep working: grant them the full scope set. Using the
    # typed JSON column binds the value correctly on both SQLite and Postgres.
    tokens = sa.table("api_tokens", sa.column("scopes", _JSON))
    op.execute(tokens.update().values(scopes=list(ALL_SCOPES)))


def downgrade() -> None:
    """Drop the scope columns."""
    op.drop_column("api_tokens", "source_allowlist")
    op.drop_column("api_tokens", "scopes")
