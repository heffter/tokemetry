"""v2 provider-neutral activity heatmap (Task 74, Gap 1).

`GET /api/v2/heatmap` returns the weekday-by-hour punch card and the daily
contribution calendar over a range, honoring the uniform v2 filter surface, so
BreakdownsView's heatmaps obey the global provider/model filter (the v1
`/api/v1/heatmap` had no provider parameter).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.query_deps import query_filters
from tokemetry_server.api.v2.schemas import (
    HeatmapCalendarCell,
    HeatmapMetadata,
    HeatmapPunchCell,
    HeatmapV2Response,
)
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.heatmap_v2 import build_heatmap
from tokemetry_server.services.query_framework import QueryFilters

router = APIRouter(prefix="/api/v2", tags=["heatmap"])

#: Default range when the caller gives no bounds (a rolling quarter).
_DEFAULT_DAYS = 90


def _applied_filters(filters: QueryFilters) -> dict[str, str]:
    named = {
        "provider": filters.provider,
        "model": filters.native_model,
        "machine": filters.machine,
        "project": filters.project,
        "environment": filters.environment,
        "outcome": filters.outcome,
    }
    return {key: value for key, value in named.items() if value is not None}


@router.get("/heatmap", response_model=HeatmapV2Response)
async def heatmap_endpoint(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> HeatmapV2Response:
    """Return the filtered punch card and calendar over the range."""
    end = date_to or datetime.now(UTC).date()
    start = date_from or (end - timedelta(days=_DEFAULT_DAYS - 1))
    heatmap = await build_heatmap(session, filters, start, end)
    return HeatmapV2Response(
        punch_card=[
            HeatmapPunchCell(weekday=cell.weekday, hour=cell.hour, value=cell.value)
            for cell in heatmap.punch_card
        ],
        calendar=[
            HeatmapCalendarCell(date=cell.day, value=cell.value)
            for cell in heatmap.calendar
        ],
        metadata=HeatmapMetadata(
            total_tokens=heatmap.total_tokens,
            date_from=heatmap.start,
            date_to=heatmap.end,
            applied_filters=_applied_filters(filters),
        ),
    )
