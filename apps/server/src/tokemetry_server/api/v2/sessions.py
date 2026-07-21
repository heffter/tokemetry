"""v2 session query endpoints (Task 66.5, FR-TRACE-010/011).

``GET /api/v2/sessions`` lists session rollups keyed by the scoped identity
(provider, source, session_id); ``GET /api/v2/sessions/{scoped_id}`` returns one
session's rollup. Scope ``query:read``. The v1 sessions endpoints keep serving
the old shape during the migration.
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
    AgentNodeOut,
    SessionAgentsResponse,
    SessionOut,
    SessionsResponse,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.agent_hierarchy import session_agents
from tokemetry_server.services.query_framework import (
    QueryFilters,
    QueryParamError,
    enforce_range_bound,
)
from tokemetry_server.services.trace_queries import (
    decode_scoped_session_id,
    list_sessions,
    session_detail,
)

router = APIRouter(prefix="/api/v2", tags=["sessions"])


@router.get("/sessions", response_model=SessionsResponse)
async def sessions_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> SessionsResponse:
    """Session rollups by scoped identity over a bounded range."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        page = await list_sessions(session, start, end, filters, cursor, limit)
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return SessionsResponse(
        sessions=[SessionOut(**dataclasses.asdict(s)) for s in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/sessions/{scoped_id}", response_model=SessionOut)
async def session_detail_endpoint(
    scoped_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> SessionOut:
    """One session's rollup by its scoped identity."""
    try:
        provider, source, session_id = decode_scoped_session_id(scoped_id)
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    row = await session_detail(session, provider, source, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    return SessionOut(**dataclasses.asdict(row))


@router.get("/sessions/{scoped_id}/agents", response_model=SessionAgentsResponse)
async def session_agents_endpoint(
    scoped_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> SessionAgentsResponse:
    """The agent hierarchy of one session (FR-TRACE-009)."""
    try:
        provider, source, session_id = decode_scoped_session_id(scoped_id)
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    nodes = await session_agents(session, provider, source, session_id)
    return SessionAgentsResponse(
        scoped_id=scoped_id,
        agents=[AgentNodeOut(**dataclasses.asdict(node)) for node in nodes],
    )
