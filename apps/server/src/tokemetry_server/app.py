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
from tokemetry_core.models import UsageEvent

from tokemetry_server.api import ingest
from tokemetry_server.config import Settings, get_settings
from tokemetry_server.db.migrate import upgrade_to_head
from tokemetry_server.db.session import create_engine, create_session_factory

#: Type of the per-event cost function stored on app state.
CostFn = Callable[[UsageEvent], "Decimal | None"]


def _no_cost(_: UsageEvent) -> Decimal | None:
    """Placeholder cost function: no price known yet (see cost engine task)."""
    return None


def create_app(settings: Settings | None = None, cost_fn: CostFn | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration; the process singleton when omitted.
        cost_fn: Per-event cost function; a no-op placeholder when omitted.
    """
    resolved = settings if settings is not None else get_settings()
    resolved_cost = cost_fn if cost_fn is not None else _no_cost

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved.auto_migrate:
            upgrade_to_head(resolved.sync_database_url)
        engine = create_engine(resolved)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.dialect_name = engine.dialect.name
        app.state.cost_fn = resolved_cost
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
