"""v2 cost query endpoints (Task 66.4, FR-QUERY-006, FR-COST-012).

``GET /api/v2/costs`` aggregates active computed costs over a bounded range
grouped by one dimension, keeping actual API spend and subscription-equivalent
value as separate series (never merged), broken down by cost status, and stamped
with the pricing version (``mixed`` when a group spans several).
``GET /api/v2/costs/reconciliation`` reports observed-versus-computed drift by
provider. Scope ``query:read``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.csv_export import CSV_FORMAT, csv_response
from tokemetry_server.api.v2.query_deps import query_filters, to_utc
from tokemetry_server.api.v2.schemas import (
    CostResponse,
    CostRowOut,
    QueryWarningOut,
    ReconciliationResponse,
    ReconciliationRowOut,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.queries_v2 import (
    USAGE_DIMENSIONS,
    cost_reconciliation,
    grouped_costs,
)
from tokemetry_server.services.query_framework import (
    QueryFilters,
    QueryParamError,
    collect_warnings,
    default_stale_before,
    enforce_range_bound,
    parse_sort,
)

router = APIRouter(prefix="/api/v2", tags=["costs"])

#: Fields ``/costs`` may be sorted by.
_COST_SORTS = frozenset({"key", "actual_spend_usd", "subscription_value_usd"})

#: The CSV header for /costs (stable column contract, FR-QUERY-009).
_COST_CSV_HEADER = (
    "key", "actual_spend_usd", "subscription_value_usd", "cost_priced_usd",
    "cost_partial_usd", "cost_estimated_usd", "unpriced_event_count", "pricing_version",
)


@router.get("/costs", response_model=CostResponse)
async def costs_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    group_by: str = Query(default="provider"),
    sort: str | None = Query(default=None),
    output_format: str = Query(default="json", alias="format"),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> CostResponse | StreamingResponse:
    """Grouped costs with dual metrics, status split, and pricing version."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
        if group_by not in USAGE_DIMENSIONS:
            raise QueryParamError(
                f"group_by {group_by!r} is not one of {sorted(USAGE_DIMENSIONS)}"
            )
        sort_spec = parse_sort(sort, _COST_SORTS, "key")
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    rows = await grouped_costs(session, group_by, start, end, filters)
    rows = sorted(
        rows, key=lambda r: getattr(r, sort_spec.field), reverse=sort_spec.descending
    )
    if output_format == CSV_FORMAT:
        return csv_response(
            "costs.csv",
            _COST_CSV_HEADER,
            (
                (
                    r.key, r.actual_spend_usd, r.subscription_value_usd,
                    r.cost_priced_usd, r.cost_partial_usd, r.cost_estimated_usd,
                    r.unpriced_event_count, r.pricing_version,
                )
                for r in rows
            ),
        )
    warnings = await collect_warnings(
        session, start, end,
        default_stale_before(datetime.now(UTC), settings.source_stale_default_seconds),
    )
    return CostResponse(
        group_by=group_by,
        rows=[CostRowOut(**dataclasses.asdict(r)) for r in rows],
        warnings=[QueryWarningOut(**dataclasses.asdict(w)) for w in warnings],
    )


@router.get("/costs/reconciliation", response_model=ReconciliationResponse)
async def reconciliation_endpoint(
    request: Request,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    filters: QueryFilters = Depends(query_filters),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> ReconciliationResponse:
    """Observed-versus-computed cost drift by provider over a bounded range."""
    settings: Settings = request.app.state.settings
    start, end = to_utc(start), to_utc(end)
    try:
        enforce_range_bound(start, end, settings.query_max_range_days)
    except QueryParamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    rows = await cost_reconciliation(session, start, end, filters)
    return ReconciliationResponse(
        rows=[ReconciliationRowOut(**dataclasses.asdict(r)) for r in rows]
    )
