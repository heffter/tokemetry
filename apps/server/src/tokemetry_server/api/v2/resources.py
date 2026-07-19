"""v2 limits, data-quality, and rollup read endpoints (Task 66.6).

Read-only, keyset-paginated listings, scope ``query:read``: ``GET /api/v2/limits``
(limit snapshots with official/estimated provenance), ``GET
/api/v2/data-quality`` (anomalies filterable by kind/subject/source/state), and
``GET /api/v2/rollups`` (daily_rollups rows for external tooling with a stable
column contract).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.csv_export import CSV_FORMAT, csv_response
from tokemetry_server.api.v2.query_deps import to_utc
from tokemetry_server.api.v2.schemas import (
    DataQualityEventOut,
    DataQualityResponse,
    LimitSnapshotOut,
    LimitsResponse,
    RollupOut,
    RollupsResponse,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.query_framework import QueryParamError, enforce_range_bound
from tokemetry_server.services.resource_queries import (
    list_data_quality,
    list_limits,
    list_rollups,
)

router = APIRouter(prefix="/api/v2", tags=["resources"])


@router.get("/limits", response_model=LimitsResponse)
async def limits_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    provider: str | None = Query(default=None),
    machine: str | None = Query(default=None),
    window_kind: str | None = Query(default=None),
    provenance: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> LimitsResponse:
    """Keyset-paginated limit snapshots over a bounded range."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        page = await list_limits(
            session, start, end, provider, machine, window_kind, provenance, cursor, limit
        )
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return LimitsResponse(
        limits=[LimitSnapshotOut.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/data-quality", response_model=DataQualityResponse)
async def data_quality_endpoint(
    kind: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    source: str | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> DataQualityResponse:
    """Keyset-paginated data-quality events filterable by kind/subject/source/state."""
    try:
        page = await list_data_quality(
            session, kind, subject, source, resolved, cursor, limit
        )
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DataQualityResponse(
        events=[DataQualityEventOut.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/rollups", response_model=RollupsResponse)
async def rollups_endpoint(
    request: Request,
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    provider: str | None = Query(default=None),
    model: str | None = Query(default=None),
    machine: str | None = Query(default=None),
    source: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    billing_mode: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    output_format: str = Query(default="json", alias="format"),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> RollupsResponse | StreamingResponse:
    """Keyset-paginated daily_rollups rows over a day range for external tooling."""
    settings: Settings = request.app.state.settings
    try:
        if end < start or (end - start) > timedelta(days=settings.query_max_range_days):
            raise QueryParamError("invalid or too-wide day range")
        page = await list_rollups(
            session, start, end, provider, model, machine, source,
            environment, billing_mode, cursor, limit,
        )
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    out = [RollupOut.model_validate(row) for row in page.items]
    if output_format == CSV_FORMAT:
        header = tuple(RollupOut.model_fields)
        return csv_response(
            "rollups.csv",
            header,
            (tuple(getattr(row, name) for name in header) for row in out),
        )
    return RollupsResponse(rollups=out, next_cursor=page.next_cursor)
