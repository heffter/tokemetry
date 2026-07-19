"""v2 attempt and logical-request query endpoints (Task 66.5, FR-TRACE-006/007/012).

``GET /api/v2/attempts`` is the keyset-paginated raw event surface (bounded
range). ``GET /api/v2/requests`` lists logical requests with their attempt-chain
aggregates, and ``GET /api/v2/requests/{provider}/{logical_request_id}`` returns
the ordered-attempt drilldown for the fallback-chain UI. Scope ``query:read``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.query_deps import query_filters, to_utc
from tokemetry_server.api.v2.schemas import (
    AttemptOut,
    AttemptsResponse,
    RequestDetailResponse,
    RequestOut,
    RequestsResponse,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.query_framework import (
    QueryFilters,
    QueryParamError,
    enforce_range_bound,
)
from tokemetry_server.services.trace_queries import (
    list_attempts,
    list_requests,
    request_detail,
)

router = APIRouter(prefix="/api/v2", tags=["trace"])


@router.get("/attempts", response_model=AttemptsResponse)
async def attempts_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    logical_request_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> AttemptsResponse:
    """Keyset-paginated newest-first listing of final attempt events."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        page = await list_attempts(
            session, start, end, filters, logical_request_id, cursor, limit
        )
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AttemptsResponse(
        attempts=[AttemptOut(**dataclasses.asdict(a)) for a in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/requests", response_model=RequestsResponse)
async def requests_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    routing_policy: str | None = Query(default=None),
    fallback_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> RequestsResponse:
    """Keyset-paginated logical requests with attempt-chain aggregates."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        page = await list_requests(
            session, start, end, routing_policy, fallback_only, cursor, limit
        )
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RequestsResponse(
        requests=[RequestOut(**dataclasses.asdict(r)) for r in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/requests/{provider}/{logical_request_id}", response_model=RequestDetailResponse
)
async def request_detail_endpoint(
    provider: str,
    logical_request_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> RequestDetailResponse:
    """The ordered-attempt drilldown for one logical request."""
    detail = await request_detail(session, provider, logical_request_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown logical request")
    return RequestDetailResponse(
        request=RequestOut(**dataclasses.asdict(detail.request)),
        attempts=[AttemptOut(**dataclasses.asdict(a)) for a in detail.attempts],
    )
