"""Programmatic Alembic access for startup and tests.

Locates the packaged ``alembic.ini`` and provides helpers to upgrade or
downgrade a database, overriding the URL so callers (server startup, test
fixtures) control the target without environment juggling.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

#: apps/server root (parents: db -> tokemetry_server -> src -> server root).
_SERVER_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _SERVER_ROOT / "alembic.ini"


def alembic_config(sync_url: str) -> Config:
    """Build an Alembic ``Config`` targeting ``sync_url``.

    Args:
        sync_url: A synchronous SQLAlchemy URL (Alembic does not use async).
    """
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


def upgrade_to_head(sync_url: str) -> None:
    """Upgrade the database at ``sync_url`` to the latest revision."""
    command.upgrade(alembic_config(sync_url), "head")


def upgrade_to_revision(sync_url: str, revision: str) -> None:
    """Upgrade the database at ``sync_url`` to a specific revision.

    Used by tests that need a schema state before a later migration (for
    example the v1-to-v2 backfill, which operates on the physical
    ``usage_events`` table that migration 0010 later replaces with a view).
    """
    command.upgrade(alembic_config(sync_url), revision)


def downgrade_to_base(sync_url: str) -> None:
    """Downgrade the database at ``sync_url`` to an empty schema."""
    command.downgrade(alembic_config(sync_url), "base")
