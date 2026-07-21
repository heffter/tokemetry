"""v2 live-overview summary for the dashboard front page (Task 73, FR-UI-001).

`GET /api/v2/summary/live-overview` replaces the Claude-oriented v1
`/api/v1/summary/now`: it accepts the uniform v2 filter surface and returns a
provider-neutral snapshot -- filtered token burn rate, per-provider live limits
with an exhaustion estimate, and today's usage by native model.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.query_deps import query_filters
from tokemetry_server.api.v2.schemas import (
    LiveOverviewResponse,
    ModelUsageLiveOut,
    ProviderLimitLiveOut,
)
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.live_overview import build_live_overview
from tokemetry_server.services.query_framework import QueryFilters

router = APIRouter(prefix="/api/v2/summary", tags=["summary"])


@router.get("/live-overview", response_model=LiveOverviewResponse)
async def live_overview_endpoint(
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> LiveOverviewResponse:
    """Return the filtered, provider-neutral live overview."""
    overview = await build_live_overview(session, filters, datetime.now(UTC))
    return LiveOverviewResponse(
        now=overview.now,
        burn_rate_per_min=overview.burn_rate_per_min,
        provider_limits=[
            ProviderLimitLiveOut(
                provider=limit.provider,
                window_kind=limit.window_kind,
                utilization_pct=limit.utilization_pct,
                limit_amount=limit.limit_amount,
                remaining=limit.remaining,
                unit=limit.unit,
                resets_at=limit.resets_at,
                predicted_exhaustion_at=limit.predicted_exhaustion_at,
            )
            for limit in overview.provider_limits
        ],
        today_by_model=[
            ModelUsageLiveOut(
                native_model=item.native_model, total_tokens=item.total_tokens
            )
            for item in overview.today_by_model
        ],
    )
