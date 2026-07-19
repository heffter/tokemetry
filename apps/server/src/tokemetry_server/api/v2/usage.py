"""v2 usage query endpoint (Task 66.4, FR-QUERY-007/008).

``GET /api/v2/usage`` aggregates final-attempt usage over a bounded time range
grouped by one dimension, returning all six token counters plus attempt counts
(never snapshots or logical-request summaries), with the uniform filters and a
data-quality warning envelope. Scope ``query:read``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.query_deps import query_filters, to_utc
from tokemetry_server.api.v2.schemas import QueryWarningOut, UsageResponse, UsageRowOut
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.queries_v2 import (
    USAGE_DIMENSIONS,
    grouped_usage,
)
from tokemetry_server.services.query_framework import (
    QueryFilters,
    QueryParamError,
    collect_warnings,
    default_stale_before,
    enforce_range_bound,
    parse_sort,
)

router = APIRouter(prefix="/api/v2", tags=["usage"])

#: Fields ``/usage`` may be sorted by.
_USAGE_SORTS = frozenset({"key", "total_tokens", "attempt_count"})


@router.get("/usage", response_model=UsageResponse)
async def usage_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    group_by: str = Query(default="day"),
    sort: str | None = Query(default=None),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> UsageResponse:
    """Grouped final-attempt usage over a bounded range with warnings."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        if group_by not in USAGE_DIMENSIONS:
            raise QueryParamError(
                f"group_by {group_by!r} is not one of {sorted(USAGE_DIMENSIONS)}"
            )
        sort_spec = parse_sort(sort, _USAGE_SORTS, "key")
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    rows = await grouped_usage(session, group_by, start, end, filters)
    rows = sorted(
        rows, key=lambda r: getattr(r, sort_spec.field), reverse=sort_spec.descending
    )
    warnings = await collect_warnings(
        session, start, end,
        default_stale_before(datetime.now(UTC), settings.source_stale_default_seconds),
    )
    return UsageResponse(
        group_by=group_by,
        rows=[UsageRowOut(**dataclasses.asdict(r)) for r in rows],
        warnings=[QueryWarningOut(**dataclasses.asdict(w)) for w in warnings],
    )
