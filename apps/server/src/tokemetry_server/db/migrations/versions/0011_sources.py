"""sources registry table

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Name of the Postgres-only FK from usage_events_v2.source_id to sources.id.
_SOURCE_FK = "fk_usage_events_v2_source_id_sources"


def upgrade() -> None:
    """Create the sources table and, on Postgres, the source_id foreign key."""
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("instance_id", sa.String(200), nullable=True),
        sa.Column("machine", sa.String(200), nullable=True),
        sa.Column("token_label", sa.String(200), nullable=True),
        sa.Column("billing_mode", sa.String(20), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.ForeignKeyConstraint(
            ["machine"], ["machines.id"], name="fk_sources_machine_machines"
        ),
        sa.UniqueConstraint("type", "name", "instance_id", name="sources_identity"),
    )
    op.create_index("ix_sources_last_seen", "sources", ["last_seen"])

    # The usage_events_v2.source_id foreign key is added only on Postgres: SQLite
    # cannot ADD CONSTRAINT to an existing table, and recreating usage_events_v2
    # in batch mode would break the usage_events compatibility view that depends
    # on it. SQLite keeps a logical reference (the service never orphans it).
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            _SOURCE_FK, "usage_events_v2", "sources", ["source_id"], ["id"]
        )


def downgrade() -> None:
    """Drop the source_id foreign key (Postgres) and the sources table."""
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_SOURCE_FK, "usage_events_v2", type_="foreignkey")
    op.drop_index("ix_sources_last_seen", table_name="sources")
    op.drop_table("sources")
