"""audit_log.request_id for HTTP correlation (Task 70.4)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-21

Adds a nullable ``request_id`` so an audit entry can be correlated with the
response's request id (FR-INGEST-016); NULL for background actions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log", sa.Column("request_id", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("audit_log", "request_id")
