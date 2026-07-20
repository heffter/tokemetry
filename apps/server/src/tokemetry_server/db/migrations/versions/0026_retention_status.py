"""retention_status table for the retention worker (Task 70.2)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-20

Per-category status the retention worker updates after each sweep (FR-RET-005):
last run, rows deleted last time and cumulatively, current backlog, and the
oldest row still retained.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retention_status",
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_deleted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "pending_backlog", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("oldest_retained", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("category", name="pk_retention_status"),
    )


def downgrade() -> None:
    op.drop_table("retention_status")
