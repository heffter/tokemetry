"""Async engine and session factory.

The application uses SQLAlchemy's async engine (asyncpg in production,
aiosqlite in development). Alembic migrations run separately with a sync
engine; see ``db/migrations/env.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tokemetry_server.config import Settings, get_settings


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create an async engine from settings (or the process singleton)."""
    resolved = settings if settings is not None else get_settings()
    return create_async_engine(resolved.database_url, future=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error.

    Intended for use as a FastAPI dependency and in background tasks.
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
