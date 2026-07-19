"""Sources API: list reporting sources with health, mutate, and revoke.

``GET /api/v2/sources`` returns the source registry joined with query-time
health (freshness, last successful ingest, error count, schema version, clock
skew), filterable by ``type`` and staleness. ``PATCH`` mutates the label and
billing mode without changing event identity (FR-SOURCE-010); ``POST
/{id}/revoke`` stops accepting a source's events without deleting history
(FR-SOURCE-012). Listing needs ``query:read``; mutation and revocation are
administrative (``admin:tokens``). No token hashes or secrets are exposed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    SourceHealthOut,
    SourceOut,
    SourceUpdateRequest,
)
from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.scopes import ADMIN_TOKENS, QUERY_READ
from tokemetry_server.services.billing_mode import BILLING_MODES
from tokemetry_server.services.sources import SourceHealth, SourceHealthService

router = APIRouter(prefix="/api/v2", tags=["sources"])


def _health_service(settings: Settings, session: AsyncSession) -> SourceHealthService:
    """Build a health service with the configured per-type staleness thresholds."""
    return SourceHealthService(
        session,
        stale_thresholds={
            "collector": settings.source_stale_collector_seconds,
            "gateway": settings.source_stale_gateway_seconds,
        },
        default_stale_seconds=settings.source_stale_default_seconds,
    )


def _to_out(source: models.Source, health: SourceHealth) -> SourceOut:
    """Project a source row plus its computed health onto the wire model."""
    return SourceOut(
        id=source.id,
        type=source.type,
        name=source.name,
        version=source.version,
        instance_id=source.instance_id,
        machine=source.machine,
        token_label=source.token_label,
        billing_mode=source.billing_mode,
        first_seen=source.first_seen,
        last_seen=source.last_seen,
        revoked=source.revoked,
        health=SourceHealthOut(
            stale=health.stale,
            last_successful_ingest=health.last_successful_ingest,
            recent_error_count=health.recent_error_count,
            reported_schema_version=health.reported_schema_version,
            clock_skew_seconds=health.clock_skew_seconds,
            staleness_threshold_seconds=health.staleness_threshold_seconds,
        ),
    )


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    request: Request,
    source_type: str | None = Query(default=None, alias="type"),
    stale: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> list[SourceOut]:
    """List sources with health, filterable by type and staleness."""
    settings: Settings = request.app.state.settings
    health_service = _health_service(settings, session)

    stmt = select(models.Source)
    if source_type is not None:
        stmt = stmt.where(models.Source.type == source_type)
    stmt = stmt.order_by(models.Source.id)
    rows = (await session.execute(stmt)).scalars().all()

    now = datetime.now(UTC)
    out: list[SourceOut] = []
    for row in rows:
        health = health_service.health(row, now)
        if stale is not None and health.stale != stale:
            continue
        out.append(_to_out(row, health))
    return out


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    payload: SourceUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(ADMIN_TOKENS)),
) -> SourceOut:
    """Mutate a source's label and billing mode (event identity unchanged)."""
    source = await session.get(models.Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown source")
    if payload.token_label is not None:
        source.token_label = payload.token_label
    if payload.billing_mode is not None:
        if payload.billing_mode not in BILLING_MODES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"billing_mode must be one of {sorted(BILLING_MODES)}",
            )
        source.billing_mode = payload.billing_mode

    settings: Settings = request.app.state.settings
    health = _health_service(settings, session).health(source)
    return _to_out(source, health)


@router.post("/sources/{source_id}/revoke", response_model=SourceOut)
async def revoke_source(
    source_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(ADMIN_TOKENS)),
) -> SourceOut:
    """Revoke a source; its history is retained (FR-SOURCE-012)."""
    source = await session.get(models.Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown source")
    source.revoked = True

    settings: Settings = request.app.state.settings
    health = _health_service(settings, session).health(source)
    return _to_out(source, health)
