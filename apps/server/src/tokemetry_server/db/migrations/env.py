"""Alembic migration environment.

Runs migrations with a synchronous engine (Alembic does not need async).
The database URL is resolved from, in order: an explicit ``sqlalchemy.url``
set on the Alembic config (used by tests), then the ``TOKEMETRY_``
settings' sync database URL.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from tokemetry_server.config import get_settings
from tokemetry_server.db import models as _models  # noqa: F401  (register tables)
from tokemetry_server.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the sync database URL for migrations."""
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return get_settings().sync_database_url


def run_migrations_offline() -> None:
    """Emit migrations as SQL without a live database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
