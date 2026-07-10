"""FastAPI application factory.

Wires configuration, the async database engine, and routers. On startup it
optionally runs migrations to head and stores the engine, session factory,
dialect name, and cost function on ``app.state`` for dependencies to use.
The cost function is a no-op placeholder here; the cost engine task injects
the real implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tokemetry_core.models import UsageEvent

from tokemetry_server.api import alerts, ingest, pricing, query, stream, tokens
from tokemetry_server.config import Settings, get_settings
from tokemetry_server.db.migrate import upgrade_to_head
from tokemetry_server.db.session import create_engine, create_session_factory
from tokemetry_server.providers import build_registry
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import build_notifiers
from tokemetry_server.services.alerting.seed import seed_default_alert_rules
from tokemetry_server.services.broadcast import Broadcaster
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


async def _alert_loop(
    engine: AlertEngine,
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
) -> None:
    """Periodically evaluate alert rules until cancelled."""
    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:
                await engine.run(session)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from loguru import logger

            logger.warning("alert evaluation failed: {}", exc)


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
        if resolved.seed_default_alerts:
            async with session_factory() as seed_session:
                await seed_default_alert_rules(seed_session)
                await seed_session.commit()
        http_client = httpx.AsyncClient(timeout=30.0)
        alert_engine = AlertEngine(
            build_notifiers(resolved, http_client), timezone=resolved.timezone
        )
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.dialect_name = dialect_name
        app.state.cost_fn = active_cost_fn
        app.state.broadcaster = Broadcaster()
        app.state.alert_engine = alert_engine
        app.state.http_client = http_client

        alert_task: asyncio.Task[None] | None = None
        if resolved.alerts_enabled:
            alert_task = asyncio.create_task(
                _alert_loop(alert_engine, session_factory, resolved.alerts_interval_seconds)
            )
        try:
            yield
        finally:
            if alert_task is not None:
                alert_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await alert_task
            await http_client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="tokemetry",
        version="0.1.0",
        summary="Self-hosted multi-machine AI token usage tracking",
        lifespan=lifespan,
    )
    app.include_router(ingest.router)
    app.include_router(query.router)
    app.include_router(pricing.router)
    app.include_router(tokens.router)
    app.include_router(alerts.router)
    app.include_router(stream.router)

    @app.get("/api/v1/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe (unauthenticated)."""
        return {"status": "ok"}

    # Serve the built dashboard SPA when configured. Mounted last so API and
    # WebSocket routes take precedence; html=True gives SPA-route fallback.
    if resolved.static_dir is not None and resolved.static_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=resolved.static_dir, html=True),
            name="spa",
        )

    return app
