"""Pricing write endpoints: add/override a price row and recompute costs.

Adding a price row alone does not change already-stored ``cost_usd`` values
(those were computed at ingest). ``recompute`` reloads the price table into a
fresh cost engine, reprices every event, refreshes the affected daily
rollups, and hot-swaps the ingest cost function so future ingests use the new
prices too.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import PriceRow

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.schemas_query import (
    PriceRowIn,
    PricingOut,
    RecomputeResult,
    SyncResult,
)
from tokemetry_server.db import models
from tokemetry_server.providers import build_registry
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.services.cost import CostEngine
from tokemetry_server.services.litellm_sync import (
    fetch_litellm_prices,
    import_rate_cards_from_data,
    sync_anthropic_pricing,
)
from tokemetry_server.services.pricing_import import apply_import, compute_import_diff
from tokemetry_server.services.pricing_repo import (
    load_pricing_table,
    recompute_costs,
    upsert_price_rows,
)
from tokemetry_server.services.rollups import refresh_rollups_for_days

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])

#: Effective date for LiteLLM-synced prices: early enough to cover all
#: retained history, so recompute prices every stored event.
_SYNC_EFFECTIVE_DATE = date(2025, 1, 1)


@router.post("", response_model=PricingOut, status_code=status.HTTP_201_CREATED)
async def create_price(
    payload: PriceRowIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> PricingOut:
    """Create or override a price row (upsert on provider/model/date)."""
    row = PriceRow(
        provider=payload.provider,
        model=payload.model,
        effective_date=payload.effective_date,
        input_per_mtok=payload.input_per_mtok,
        output_per_mtok=payload.output_per_mtok,
        cache_read_per_mtok=payload.cache_read_per_mtok,
        cache_write_short_per_mtok=payload.cache_write_short_per_mtok,
        cache_write_long_per_mtok=payload.cache_write_long_per_mtok,
    )
    await upsert_price_rows(session, request.app.state.dialect_name, [row], payload.source)
    return PricingOut(
        provider=payload.provider,
        model=payload.model,
        effective_date=payload.effective_date,
        input_per_mtok=payload.input_per_mtok,
        output_per_mtok=payload.output_per_mtok,
        cache_read_per_mtok=payload.cache_read_per_mtok,
        cache_write_short_per_mtok=payload.cache_write_short_per_mtok,
        cache_write_long_per_mtok=payload.cache_write_long_per_mtok,
        source=payload.source,
    )


@router.post("/sync-litellm", response_model=SyncResult)
async def sync_litellm(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> SyncResult:
    """Fetch LiteLLM's price database and upsert Anthropic rows.

    Prices are stored with an early effective date so they apply to all
    historical events (equivalent-cost estimation for a subscriber, where
    prices rarely change). Does not recompute stored costs; call
    ``/recompute`` afterward.

    For v2 compatibility this also feeds the ``rate_cards`` table by running the
    v2 import (dry run plus immediate auto-apply, labeled ``v1_sync`` in the
    audit log), so the legacy endpoint keeps both pricing stacks current.
    """
    data = await fetch_litellm_prices(request.app.state.http_client)
    synced = await sync_anthropic_pricing(
        session, request.app.state.dialect_name, data, _SYNC_EFFECTIVE_DATE
    )
    now = datetime.now(UTC)
    rows = import_rate_cards_from_data(data, _SYNC_EFFECTIVE_DATE, now)
    diff = await compute_import_diff(session, rows, _SYNC_EFFECTIVE_DATE)
    await apply_import(
        session, rows, _SYNC_EFFECTIVE_DATE, diff.digest,
        actor="v1_sync", source_label="litellm+official (v1_sync)", now=now,
    )
    return SyncResult(synced=synced)


@router.post("/recompute", response_model=RecomputeResult)
async def recompute(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> RecomputeResult:
    """Reprice all events and rollups against the current price table.

    Also hot-swaps the ingest cost function so subsequent ingests use the
    updated prices without a restart.
    """
    table = await load_pricing_table(session)
    engine = CostEngine(table, build_registry())

    updated = await recompute_costs(session, engine)
    refreshed = await refresh_rollups_for_days(
        session,
        request.app.state.dialect_name,
        await _event_days(session),
        request.app.state.settings.project_root_markers,
    )

    request.app.state.cost_fn = engine.cost
    return RecomputeResult(events_updated=updated, rollups_refreshed=refreshed)


async def _event_days(session: AsyncSession) -> list[date]:
    """Return every calendar day (UTC) spanned by stored events."""
    event = models.UsageEvent
    span = (await session.execute(select(func.min(event.ts), func.max(event.ts)))).one()
    low, high = span
    if low is None or high is None:
        return []
    start = _as_utc(low).date()
    end = _as_utc(high).date()
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _as_utc(value: datetime) -> datetime:
    """Ensure a DB datetime is timezone-aware (UTC)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
