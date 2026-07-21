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
import math
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint
from tokemetry_core.models import UsageEvent

from tokemetry_server.api import alerts, ingest, pricing, query, stream, tokens, v2
from tokemetry_server.api.security import (
    HSTS_VALUE,
    SECURE_HEADERS,
    is_api_path,
    is_health_path,
    is_ingest_path,
    rate_key,
)
from tokemetry_server.config import Settings, get_settings
from tokemetry_server.db.migrate import upgrade_to_head
from tokemetry_server.db.session import create_engine, create_session_factory
from tokemetry_server.providers import build_registry
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import build_notifiers
from tokemetry_server.services.alerting.seed import seed_default_alert_rules
from tokemetry_server.services.broadcast import Broadcaster
from tokemetry_server.services.channel_config import resolve_channel_settings
from tokemetry_server.services.cost import CostEngine
from tokemetry_server.services.cost_worker import sweep_uncosted_costs
from tokemetry_server.services.pricing_repo import load_pricing_table, seed_default_pricing
from tokemetry_server.services.rate_limit import RateLimiter
from tokemetry_server.services.registries import seed_default_providers
from tokemetry_server.services.registry_backfill import RegistryBackfill
from tokemetry_server.services.retention import resolve_retention_policy
from tokemetry_server.services.retention_worker import run_retention_sweep

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


async def _cost_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    batch_size: int,
    billing_mode_overrides: Mapping[str, str],
) -> None:
    """Periodically price uncosted events out of the ingest path (FR-COST-009)."""
    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:
                await sweep_uncosted_costs(session, batch_size, billing_mode_overrides)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from loguru import logger

            logger.warning("cost worker sweep failed: {}", exc)


async def _retention_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    batch_size: int,
    dedup_window_seconds: float,
) -> None:
    """Periodically delete rows past their retention policy (Task 70.2)."""
    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:
                policy = await resolve_retention_policy(session)
                await run_retention_sweep(
                    session,
                    policy,
                    datetime.now(UTC),
                    batch_size=batch_size,
                    dedup_window_seconds=dedup_window_seconds,
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from loguru import logger

            logger.warning("retention sweep failed: {}", exc)


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
        async with session_factory() as registry_session:
            await seed_default_providers(registry_session)
            await RegistryBackfill(
                registry_session, resolved.data_quality_dedup_window_seconds
            ).run()
            await registry_session.commit()
        if resolved.seed_default_alerts:
            async with session_factory() as seed_session:
                await seed_default_alert_rules(seed_session)
                await seed_session.commit()
        http_client = httpx.AsyncClient(timeout=30.0)
        async with session_factory() as channel_session:
            effective = await resolve_channel_settings(channel_session, resolved)
        alert_engine = AlertEngine(
            build_notifiers(effective, http_client),
            timezone=resolved.timezone,
            stale_thresholds={
                "collector": resolved.source_stale_collector_seconds,
                "gateway": resolved.source_stale_gateway_seconds,
            },
            default_stale_seconds=resolved.source_stale_default_seconds,
        )
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.dialect_name = dialect_name
        app.state.cost_fn = active_cost_fn
        app.state.broadcaster = Broadcaster()
        app.state.alert_engine = alert_engine
        app.state.http_client = http_client
        app.state.ingest_rate_limiter = RateLimiter(
            resolved.ingest_rate_capacity, resolved.ingest_rate_per_second
        )
        app.state.query_rate_limiter = RateLimiter(
            resolved.query_rate_capacity, resolved.query_rate_per_second
        )
        # Live count of open WebSocket connections per token, for the per-token
        # connection cap (NFR-SEC-003).
        app.state.ws_connections = {}

        alert_task: asyncio.Task[None] | None = None
        if resolved.alerts_enabled:
            alert_task = asyncio.create_task(
                _alert_loop(alert_engine, session_factory, resolved.alerts_interval_seconds)
            )
        cost_task: asyncio.Task[None] | None = None
        if resolved.cost_worker_enabled:
            cost_task = asyncio.create_task(
                _cost_loop(
                    session_factory,
                    resolved.cost_worker_interval_seconds,
                    resolved.cost_worker_batch_size,
                    resolved.billing_mode_override_map,
                )
            )
        retention_task: asyncio.Task[None] | None = None
        if resolved.retention_worker_enabled:
            retention_task = asyncio.create_task(
                _retention_loop(
                    session_factory,
                    resolved.retention_worker_interval_seconds,
                    resolved.retention_worker_batch_size,
                    resolved.data_quality_dedup_window_seconds,
                )
            )
        try:
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention_task
            if cost_task is not None:
                cost_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cost_task
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
        description=(
            "Two API versions coexist during the provider-neutral migration "
            "(FR-INGEST-011/014). **v1** (`/api/v1/*`) is the stable collector "
            "surface and stays wire-compatible for the whole program. **v2** "
            "(`/api/v2/*`) is the provider-neutral surface: batch ingest "
            "(`/ingest/events|limits|aggregates`), pre-flight `validate`, the "
            "published usage-event JSON schema (`/schemas/usage-event`), and "
            "read-only registries. Version is selected by URL path (there is no "
            "content negotiation); clients pin a major version and a bearer "
            "token, and every response carries an `X-Request-ID`."
        ),
        lifespan=lifespan,
    )

    # Restrictive CORS: no cross-origin access unless an allowlist is set. The
    # dashboard is served same-origin, so the default grants nothing (NFR-SEC-007).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allow_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _security_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Enforce request-size and query rate limits, and add secure headers.

        Request bodies over ``max_request_bytes`` are refused with 413
        (NFR-SEC-004). Non-ingest API traffic is rate-limited by the query
        bucket keyed per credential (ingest has its own per-endpoint bucket), so
        an ingest burst never starves query reads (FR-INGEST-015); a denied
        request gets 429 with ``Retry-After``. Every response carries the static
        secure headers (NFR-SEC-006).
        """
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > resolved.max_request_bytes
            except ValueError:
                too_large = False
            if too_large:
                return JSONResponse(
                    {"detail": "request body too large"}, status_code=413
                )

        path = request.url.path
        if is_api_path(path) and not is_ingest_path(path) and not is_health_path(path):
            retry_after = request.app.state.query_rate_limiter.check(rate_key(request))
            if retry_after is not None:
                return JSONResponse(
                    {"detail": "query rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
                )

        response = await call_next(request)
        for header, value in SECURE_HEADERS.items():
            response.headers.setdefault(header, value)
        if resolved.enable_hsts:
            response.headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
        return response

    @app.middleware("http")
    async def _stamp_request_id(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Attach a request id to state and every response (FR-INGEST-016).

        Honors a client-supplied ``X-Request-ID`` when present, otherwise
        generates one, so ingest batches and responses share a correlation id.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(ingest.router)
    app.include_router(query.router)
    app.include_router(pricing.router)
    app.include_router(tokens.router)
    app.include_router(alerts.router)
    app.include_router(stream.router)
    app.include_router(v2.router)

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
