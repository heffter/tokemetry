"""Read API: summary, limits, blocks, usage, sessions, machines, cost.

Every endpoint requires a bearer token and takes an explicit or defaulted
day range. Aggregations are delegated to the services layer; this module
handles HTTP concerns (params, defaults, response shaping) only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import require_token
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.schemas_query import (
    AnomalyOut,
    AnomalyReportOut,
    BlockOut,
    CostResponse,
    HeatmapResponse,
    LimitOut,
    MachineOut,
    OverviewOut,
    PredictionOut,
    PricingOut,
    PunchCell,
    RebuildResult,
    SessionDetailOut,
    SessionEventOut,
    SessionOut,
    SessionStatsOut,
    SummaryNow,
    TodaySummary,
    UsageBucketOut,
    UsageResponse,
)
from tokemetry_server.config import Settings, get_settings
from tokemetry_server.db import models
from tokemetry_server.services import analytics, insights, queries, rollups

router = APIRouter(prefix="/api/v1", tags=["query"])

#: Default look-back for range queries.
_DEFAULT_DAYS = 30


def _default_range(
    date_from: date | None, date_to: date | None
) -> tuple[date, date]:
    """Resolve a (start, end) day range with a 30-day default."""
    end = date_to if date_to is not None else datetime.now(UTC).date()
    start = date_from if date_from is not None else end - timedelta(days=_DEFAULT_DAYS)
    return start, end


def _bucket_out(bucket: queries.UsageBucket) -> UsageBucketOut:
    """Map a service usage bucket to its response schema."""
    return UsageBucketOut(
        key=bucket.key,
        input_tokens=bucket.input_tokens,
        output_tokens=bucket.output_tokens,
        cache_read_tokens=bucket.cache_read_tokens,
        cache_write_short_tokens=bucket.cache_write_short_tokens,
        cache_write_long_tokens=bucket.cache_write_long_tokens,
        total_tokens=bucket.total_tokens,
        cost_usd=bucket.cost_usd,
    )


def _limit_out(
    snapshot: models.LimitSnapshot, now: datetime, *, roll_reset: bool = True
) -> LimitOut:
    """Map a limit snapshot ORM row to its response schema.

    ``now`` anchors the snapshot-age and reset-rollover computations. History
    rows pass ``roll_reset=False`` since their resets are points in the past,
    not a live window to advance.
    """
    ts_aware = snapshot.ts if snapshot.ts.tzinfo else snapshot.ts.replace(tzinfo=UTC)
    age = max(0, int((now - ts_aware).total_seconds()))
    if roll_reset:
        resets_at, derived = analytics.roll_reset_forward(
            snapshot.window_kind, snapshot.resets_at, now
        )
    else:
        resets_at, derived = snapshot.resets_at, False
    return LimitOut(
        provider=snapshot.provider,
        window_kind=snapshot.window_kind,
        utilization_pct=float(snapshot.utilization_pct),
        resets_at=resets_at,
        ts=snapshot.ts,
        provenance=snapshot.provenance,
        age_seconds=age,
        derived_reset=derived,
    )


@router.get("/summary/now", response_model=SummaryNow)
async def summary_now(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> SummaryNow:
    """Return the dashboard front-page summary."""
    now = datetime.now(UTC)
    today = now.date()
    limits = await analytics.current_limits(session)
    burn = await analytics.token_burn_rate(session, now=now)
    prediction = await analytics.predict_exhaustion(session, now=now)
    by_model = await queries.usage_grouped(session, "model", today, today)
    total_tokens = sum(bucket.total_tokens for bucket in by_model)
    costs = [bucket.cost_usd for bucket in by_model if bucket.cost_usd is not None]

    return SummaryNow(
        now=now,
        limits=[_limit_out(item, now) for item in limits],
        token_burn_rate_per_min=burn,
        prediction=(
            PredictionOut(
                window_kind=prediction.window_kind,
                utilization_pct=prediction.utilization_pct,
                slope_pct_per_min=prediction.slope_pct_per_min,
                predicted_exhaustion_at=prediction.predicted_exhaustion_at,
                resets_at=analytics.roll_reset_forward(
                    prediction.window_kind, prediction.resets_at, now
                )[0],
            )
            if prediction is not None
            else None
        ),
        today=TodaySummary(
            total_tokens=total_tokens,
            cost_usd=sum(costs, Decimal("0")) if costs else None,
            by_model=[_bucket_out(bucket) for bucket in by_model],
        ),
    )


@router.get("/summary/overview", response_model=OverviewOut)
async def summary_overview(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> OverviewOut:
    """Return all-time token/cost totals and the activity span."""
    data = await queries.overview(session)
    return OverviewOut(
        input_tokens=data.input_tokens,
        output_tokens=data.output_tokens,
        cache_read_tokens=data.cache_read_tokens,
        cache_write_short_tokens=data.cache_write_short_tokens,
        cache_write_long_tokens=data.cache_write_long_tokens,
        total_tokens=data.total_tokens,
        cost_usd=data.cost_usd,
        session_count=data.session_count,
        machine_count=data.machine_count,
        first_event=data.first_event,
        last_event=data.last_event,
    )


@router.get("/limits/current", response_model=list[LimitOut])
async def limits_current(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[LimitOut]:
    """Return the latest snapshot for each limit window."""
    now = datetime.now(UTC)
    return [_limit_out(item, now) for item in await analytics.current_limits(session)]


@router.get("/limits/history", response_model=list[LimitOut])
async def limits_history(
    window_kind: str = Query(..., min_length=1),
    hours: int = Query(24, ge=1, le=720),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[LimitOut]:
    """Return a window's utilization history over the last ``hours``."""
    now = datetime.now(UTC)
    snapshots = await analytics.limits_history(
        session, window_kind, now - timedelta(hours=hours), now
    )
    return [_limit_out(item, now, roll_reset=False) for item in snapshots]


@router.get("/blocks", response_model=list[BlockOut])
async def blocks(
    hours: int = Query(120, ge=5, le=2400),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[BlockOut]:
    """Return reconstructed 5-hour usage blocks over the last ``hours``."""
    now = datetime.now(UTC)
    computed = await analytics.blocks(session, now - timedelta(hours=hours), now)
    return [
        BlockOut(
            start=block.start,
            end=block.end,
            total_tokens=block.total_tokens,
            cost_usd=block.cost_usd,
            peak_tokens_per_min=block.peak_tokens_per_min,
            end_utilization_pct=block.end_utilization_pct,
        )
        for block in computed
    ]


@router.get("/usage", response_model=UsageResponse)
async def usage(
    group_by: str = Query("day"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    provider: str | None = None,
    machine: str | None = None,
    model: str | None = None,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> UsageResponse:
    """Aggregate usage over a day range grouped by one dimension."""
    start, end = _default_range(date_from, date_to)
    buckets = await queries.usage_grouped(
        session, group_by, start, end, provider, machine, model, project
    )
    return UsageResponse(
        group_by=group_by,
        start=start,
        end=end,
        buckets=[_bucket_out(bucket) for bucket in buckets],
    )


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> list[SessionOut]:
    """Return recent sessions, newest first."""
    return [
        SessionOut(
            session_id=item.session_id,
            provider=item.provider,
            machine=item.machine,
            project=item.project,
            started_at=item.started_at,
            last_at=item.last_at,
            message_count=item.message_count,
            total_tokens=item.total_tokens,
            cost_usd=item.cost_usd,
        )
        for item in await queries.list_sessions(
            session, limit, settings.project_root_markers
        )
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def session_detail(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> SessionDetailOut:
    """Return one session's event series and efficiency stats (metadata only)."""
    detail = await queries.session_detail(
        session, session_id, settings.project_root_markers
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return SessionDetailOut(
        session_id=detail.session_id,
        project=detail.project,
        machine=detail.machine,
        message_count=len(detail.events),
        total_tokens=sum(e.total_tokens for e in detail.events),
        events=[
            SessionEventOut(
                ts=e.ts,
                model=e.model,
                input_tokens=e.input_tokens,
                output_tokens=e.output_tokens,
                cache_read_tokens=e.cache_read_tokens,
                cache_write_short_tokens=e.cache_write_short_tokens,
                cache_write_long_tokens=e.cache_write_long_tokens,
                total_tokens=e.total_tokens,
                cost_usd=e.cost_usd,
            )
            for e in detail.events
        ],
        stats=SessionStatsOut(
            tokens_per_turn=detail.stats.tokens_per_turn,
            cache_hit_rate=detail.stats.cache_hit_rate,
            context_growth=detail.stats.context_growth,
            inflection_index=detail.stats.inflection_index,
        ),
    )


@router.get("/insights/anomalies", response_model=AnomalyReportOut)
async def insights_anomalies(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> AnomalyReportOut:
    """Return sessions that deviate from the account's own usage baseline."""
    report = await insights.detect_anomalies(session, settings.project_root_markers)
    return AnomalyReportOut(
        enough_data=report.enough_data,
        session_count=report.session_count,
        anomalies=[
            AnomalyOut(
                session_id=a.session_id,
                project=a.project,
                reasons=a.reasons,
                severity_score=a.severity_score,
                total_tokens=a.total_tokens,
                cost_usd=a.cost_usd,
                cache_hit_rate=a.cache_hit_rate,
            )
            for a in report.anomalies
        ],
    )


@router.post("/admin/rebuild-rollups", response_model=RebuildResult)
async def rebuild_rollups(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> RebuildResult:
    """Delete derived rollups and rebuild them, re-applying project grouping.

    Run after changing the project-grouping configuration so historical
    breakdowns regroup (worktrees and case-variant paths fold together).
    """
    rebuilt = await rollups.rebuild_all_rollups(
        session, request.app.state.dialect_name, settings.project_root_markers
    )
    return RebuildResult(rollups_rebuilt=rebuilt)


@router.get("/machines", response_model=list[MachineOut])
async def machines(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[MachineOut]:
    """Return every registered machine with usage totals."""
    return [
        MachineOut(
            id=item.id,
            platform=item.platform,
            last_seen=item.last_seen,
            collector_version=item.collector_version,
            total_tokens=item.total_tokens,
            event_count=item.event_count,
        )
        for item in await queries.list_machines(session)
    ]


@router.get("/heatmap", response_model=HeatmapResponse)
async def heatmap(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    machine: str | None = None,
    project: str | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> HeatmapResponse:
    """Return calendar (daily) and punch-card (weekday x hour) usage."""
    start, end = _default_range(date_from, date_to)
    calendar = await queries.usage_grouped(
        session, "day", start, end, machine=machine, project=project
    )
    card = await queries.punch_card(
        session, start, end, machine, project, settings.project_root_markers
    )
    return HeatmapResponse(
        calendar=[_bucket_out(bucket) for bucket in calendar],
        punch_card=[
            PunchCell(weekday=weekday, hour=hour, total_tokens=tokens)
            for (weekday, hour), tokens in sorted(card.items())
        ],
    )


@router.get("/cost", response_model=CostResponse)
async def cost(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(require_token),
) -> CostResponse:
    """Return total cost over a range and the subscription value multiple."""
    start, end = _default_range(date_from, date_to)
    total = await queries.total_cost(session, start, end)
    monthly = settings.subscription_monthly_usd
    days = (end - start).days + 1
    multiple: float | None = None
    if monthly:
        prorated = monthly * days / 30.0
        if prorated > 0:
            multiple = float(total) / prorated
    return CostResponse(
        start=start,
        end=end,
        total_cost_usd=total,
        subscription_monthly_usd=monthly,
        value_multiple=multiple,
    )


@router.get("/pricing", response_model=list[PricingOut])
async def pricing(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[PricingOut]:
    """Return the pricing table."""
    return [
        PricingOut(
            provider=row.provider,
            model=row.model,
            effective_date=row.effective_date,
            input_per_mtok=row.input_per_mtok,
            output_per_mtok=row.output_per_mtok,
            cache_read_per_mtok=row.cache_read_per_mtok,
            cache_write_short_per_mtok=row.cache_write_short_per_mtok,
            cache_write_long_per_mtok=row.cache_write_long_per_mtok,
            source=row.source,
        )
        for row in await queries.list_pricing(session)
    ]
