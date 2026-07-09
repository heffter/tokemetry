"""FastAPI application factory.

Wires configuration, the async database engine, and routers. On startup it
optionally runs migrations to head and stores the engine, session factory,
dialect name, and cost function on ``app.state`` for dependencies to use.
The cost function is a no-op placeholder here; the cost engine task injects
the real implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tokemetry_core.models import UsageEvent

from tokemetry_server.api import ingest
from tokemetry_server.config import Settings, get_settings
from tokemetry_server.db.migrate import upgrade_to_head
from tokemetry_server.db.session import create_engine, create_session_factory
from tokemetry_server.providers import build_registry
from tokemetry_server.services.cost import CostEngine
from tokemetry_server.services.pricing_repo import load_pricing_table, seed_default_pricing

#: Type of the per-event cost function stored on app state.
CostFn = Callable[[UsageEvent], "Decimal | None"]


async def _build_cost_engine(
    session_factory: async_sessionmaker[AsyncSession], dialect_name: str
) -> CostEngine:
    """Seed default prices, load the table, and build the cost engine."""
    async with session_factory() as session:
        await seed_default_pricing(session, dialect_name)
        await session.commit()
        table = await load_pricing_table(session)
    return CostEngine(table, build_registry())


def create_app(settings: Settings | None = None, cost_fn: CostFn | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration; the process singleton when omitted.
        cost_fn: Per-event cost function; overrides the built-in cost engine
            (used by tests). When omitted the real engine is built at
            startup from the seeded pricing table.
    """
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved.auto_migrate:
            upgrade_to_head(resolved.sync_database_url)
        engine = create_engine(resolved)
        session_factory = create_session_factory(engine)
        dialect_name = engine.dialect.name
        if cost_fn is not None:
            active_cost_fn: CostFn = cost_fn
        else:
            active_cost_fn = (await _build_cost_engine(session_factory, dialect_name)).cost
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.dialect_name = dialect_name
        app.state.cost_fn = active_cost_fn
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="tokemetry",
        version="0.1.0",
        summary="Self-hosted multi-machine AI token usage tracking",
        lifespan=lifespan,
    )
    app.include_router(ingest.router)

    @app.get("/api/v1/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe (unauthenticated)."""
        return {"status": "ok"}

    return app
