"""ingest_batches operational ledger

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ingest_batches operational-traceability table."""
    op.create_table(
        "ingest_batches",
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("token_label", sa.String(200), nullable=True),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("duplicate", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("corrected", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("batch_id", name="pk_ingest_batches"),
    )
    op.create_index("ix_ingest_batches_received_at", "ingest_batches", ["received_at"])


def downgrade() -> None:
    """Drop the ingest_batches table and its index."""
    op.drop_index("ix_ingest_batches_received_at", table_name="ingest_batches")
    op.drop_table("ingest_batches")
