"""Alert rule evaluators.

Each rule ``kind`` maps to an async evaluator that inspects current state and
returns an :class:`AlertFinding` when the condition is met, or None. Rules are
data (rows in ``alert_rules``); this module is the logic they select.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import analytics
from tokemetry_server.services.alerting.filters import (
    AlertFilters,
    apply_ledger_filters,
    filters_from_config,
)
from tokemetry_server.services.sources import (
    DEFAULT_STALE_SECONDS,
    SourceHealthService,
)

#: The token counters a burn-rate window sums (reasoning is excluded, matching
#: analytics.token_burn_rate).
_BURN_RATE_WINDOW_MINUTES = 60

#: Default critical staleness as a multiple of the warn threshold when a
#: ``stale_source`` rule sets no explicit ``crit_threshold`` (mirrors the
#: ``collector_stale`` default ratio of 30 -> 120 minutes).
_STALE_CRIT_MULTIPLE = 4.0

#: Trailing window (days) the accounting-gap evaluators (``unpriced_events``,
#: ``unknown_model``) scan for recent gaps.
_ACCOUNTING_WINDOW_DAYS = 1

#: How many top offending (provider, model) pairs an accounting alert names.
_TOP_OFFENDERS = 5

#: Cost statuses that mean an event still lacks a full price (mirrors
#: services.pricing_admin._UNPRICED_STATUSES).
_UNPRICED_STATUSES = ("unpriced", "partial")

#: Registry lifecycles that count as a *known* model; anything else (including a
#: missing registry row) is treated as unknown for the ``unknown_model`` alert.
_KNOWN_LIFECYCLES = ("active", "deprecated", "retired")


async def _ledger_burn_rate(
    session: AsyncSession, now: datetime, filters: AlertFilters
) -> float:
    """Tokens/min over the trailing window from usage_events_v2, filter-scoped.

    Sums final attempts only, so with empty filters this matches the v1-view
    ``analytics.token_burn_rate`` the unfiltered path uses.
    """
    since = now - timedelta(minutes=_BURN_RATE_WINDOW_MINUTES)
    event = models.UsageEventV2
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    statement = select(func.coalesce(func.sum(total), 0)).where(
        event.event_kind == "attempt",
        event.finality == "final",
        event.ts_started >= since,
    )
    statement = apply_ledger_filters(statement, filters)
    summed = int((await session.execute(statement)).scalar_one() or 0)
    return summed / _BURN_RATE_WINDOW_MINUTES


@dataclass(frozen=True)
class AlertFinding:
    """A fired alert: what to say and how severe."""

    severity: str
    title: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityFinding:
    """A per-entity finding: a stable entity key plus the finding it fired.

    Grouped rule kinds (e.g. ``stale_source``) return one of these per affected
    entity so the engine can track firing state, cooldown, and recovery for each
    entity independently. ``key`` is a content-free stable identifier (the
    source id for ``stale_source``).
    """

    key: str
    finding: AlertFinding


def _threshold(rule: models.AlertRule, default: float) -> float:
    """Read a rule's warn threshold (or legacy single threshold), or a default."""
    if rule.warn_threshold is not None:
        return float(rule.warn_threshold)
    return float(rule.threshold) if rule.threshold is not None else default


def _warn_crit(
    rule: models.AlertRule, warn_default: float, crit_default: float
) -> tuple[float, float]:
    """Resolve a rule's (warn, crit) thresholds with per-kind defaults.

    ``warn`` falls back to the legacy single ``threshold``; ``crit`` falls back
    to its default when unset. ``crit`` is floored at ``warn`` so the ordering
    is always warn <= crit.
    """
    warn = _threshold(rule, warn_default)
    crit = float(rule.crit_threshold) if rule.crit_threshold is not None else crit_default
    return warn, max(warn, crit)


def _severity_for(value: float, warn: float, crit: float) -> str | None:
    """Return the severity a measured value crosses, or None below warn."""
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return None


async def _limit_pct(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when a limit window's utilization crosses a warn/crit threshold.

    Limit snapshots carry only a provider dimension, so a rule's ``provider``
    filter is honored here; the other dimensions do not apply to limit windows.
    """
    warn, crit = _warn_crit(rule, 80.0, 95.0)
    window = rule.window_kind or "five_hour"
    filters = filters_from_config(rule.config)
    scoped = ["provider"] if filters.provider else []
    for snapshot in await analytics.current_limits(session):
        if snapshot.window_kind != window:
            continue
        if filters.provider and snapshot.provider not in filters.provider:
            continue
        pct = float(snapshot.utilization_pct)
        severity = _severity_for(pct, warn, crit)
        if severity is not None:
            crossed = crit if severity == "critical" else warn
            return AlertFinding(
                severity=severity,
                title=f"{window} at {pct:.0f}%",
                body=f"Utilization {pct:.1f}% has crossed the {crossed:.0f}% threshold.",
                context={
                    "window_kind": window,
                    "utilization_pct": pct,
                    "scoped_dimensions": scoped,
                },
            )
    return None


async def _predicted_exhaustion(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when the 5-hour window is predicted to exhaust before its reset."""
    prediction = await analytics.predict_exhaustion(session, now=now)
    if prediction is None or prediction.predicted_exhaustion_at is None:
        return None
    if prediction.resets_at is None:
        return None
    if prediction.predicted_exhaustion_at < prediction.resets_at:
        return AlertFinding(
            severity="warning",
            title="Predicted to hit the limit before reset",
            body=(
                f"At the current pace the 5-hour block reaches 100% at "
                f"{prediction.predicted_exhaustion_at:%H:%M}, before it resets at "
                f"{prediction.resets_at:%H:%M}."
            ),
            context={
                "predicted_exhaustion_at": prediction.predicted_exhaustion_at.isoformat(),
                "resets_at": prediction.resets_at.isoformat(),
            },
        )
    return None


async def _burn_rate(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when the token burn rate crosses a warn/crit threshold."""
    warn, crit = _warn_crit(rule, 5000.0, 10000.0)
    filters = filters_from_config(rule.config)
    # Unfiltered rules keep the exact v1-view path; scoped rules read the ledger.
    if filters.is_empty:
        rate = await analytics.token_burn_rate(session, now=now)
    else:
        rate = await _ledger_burn_rate(session, now, filters)
    severity = _severity_for(rate, warn, crit)
    if severity is not None:
        crossed = crit if severity == "critical" else warn
        return AlertFinding(
            severity=severity,
            title="High burn rate",
            body=f"Burning {rate:.0f} tokens/min (threshold {crossed:.0f}).",
            context={
                "burn_rate_per_min": rate,
                "scoped_dimensions": filters.scoped_dimensions(),
            },
        )
    return None


async def _collector_stale(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when a machine's silence crosses a warn/crit staleness threshold."""
    warn, crit = _warn_crit(rule, 30.0, 120.0)
    cutoff = now - timedelta(minutes=warn)
    result = await session.execute(
        select(models.Machine.id, models.Machine.last_seen).where(
            models.Machine.last_seen.is_not(None), models.Machine.last_seen < cutoff
        )
    )
    rows = result.all()
    if not rows:
        return None
    stale = [row[0] for row in rows]
    worst = max(
        (now - (seen if seen.tzinfo else seen.replace(tzinfo=UTC))).total_seconds() / 60.0
        for _, seen in rows
    )
    severity = _severity_for(worst, warn, crit) or "warning"
    return AlertFinding(
        severity=severity,
        title="Collector stale",
        body=f"No data from {', '.join(stale)} for over {warn:.0f} minutes.",
        context={"machines": stale, "worst_stale_minutes": round(worst, 1)},
    )


def _top_offenders(counts: dict[tuple[str, str], int]) -> list[dict[str, Any]]:
    """The top offending (provider, model) pairs by event count, content-free.

    Provider and model are catalog identifiers (never usage content), so an
    operator can see which models to price without exposing any event content.
    """
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"provider": provider, "model": model, "count": count}
        for (provider, model), count in ranked[:_TOP_OFFENDERS]
    ]


async def _open_dq_count(session: AsyncSession, kind: str) -> int:
    """Count open (unresolved) data-quality records of ``kind`` (a link, not a scan)."""
    dq = models.DataQualityEvent
    statement = (
        select(func.count())
        .select_from(dq)
        .where(dq.kind == kind, dq.resolved.is_(False))
    )
    return int((await session.execute(statement)).scalar_one())


async def _unpriced_events(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when active costs are unpriced or partial over the window (FR-ALERT-004).

    Counts ``computed_costs`` rows still marked ``unpriced`` or ``partial`` for
    events in the trailing window, honoring the rule's dimension filters. A known
    model that simply lacks a rate card lands here (its cost is unpriced), which
    is distinct from an unknown model (see :func:`_unknown_model`). Context names
    the top offending (provider, model) pairs so an operator knows what to price.
    """
    since = now - timedelta(days=_ACCOUNTING_WINDOW_DAYS)
    filters = filters_from_config(rule.config)
    warn, crit = _warn_crit(rule, 1.0, 100.0)
    cost = models.ComputedCost
    event = models.UsageEventV2
    statement = (
        select(event.provider, event.native_model, func.count().label("n"))
        .select_from(cost)
        .join(event, (cost.provider == event.provider) & (cost.event_id == event.event_id))
        .where(
            cost.active.is_(True),
            cost.cost_status.in_(_UNPRICED_STATUSES),
            event.ts_started >= since,
        )
        .group_by(event.provider, event.native_model)
    )
    statement = apply_ledger_filters(statement, filters)
    counts = {
        (row.provider, row.native_model): int(row.n)
        for row in await session.execute(statement)
    }
    total = sum(counts.values())
    severity = _severity_for(float(total), warn, crit)
    if severity is None:
        return None
    crossed = crit if severity == "critical" else warn
    return AlertFinding(
        severity=severity,
        title="Unpriced usage",
        body=(
            f"{total} events in the last {_ACCOUNTING_WINDOW_DAYS}d are unpriced or "
            f"partially priced (threshold {crossed:.0f})."
        ),
        context={
            "unpriced_events": total,
            "top_offenders": _top_offenders(counts),
            "open_data_quality_events": await _open_dq_count(session, "unpriced_usage"),
            "scoped_dimensions": filters.scoped_dimensions(),
        },
    )


async def _unknown_model(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when recent events used a model the registry does not know (FR-ALERT-005).

    Reworked off the old NULL-cost heuristic onto the model registry's lifecycle
    signal: an event counts when its ``(provider, native_model)`` has no registry
    row, or a row whose ``lifecycle`` is ``unknown`` -- the same signal the
    ``unknown_model`` data-quality records track. A known-but-unpriced model does
    not count here (that is :func:`_unpriced_events`). Honors the rule's
    dimension filters; context names the top offending (provider, model) pairs.
    """
    since = now - timedelta(days=_ACCOUNTING_WINDOW_DAYS)
    filters = filters_from_config(rule.config)
    warn, crit = _warn_crit(rule, 1.0, 25.0)
    event = models.UsageEventV2
    model = models.Model
    statement = (
        select(event.provider, event.native_model, func.count().label("n"))
        .select_from(event)
        .outerjoin(
            model,
            (model.provider == event.provider)
            & (model.native_model_id == event.native_model),
        )
        .where(
            event.event_kind == "attempt",
            event.finality == "final",
            event.ts_started >= since,
            or_(model.native_model_id.is_(None), model.lifecycle.notin_(_KNOWN_LIFECYCLES)),
        )
        .group_by(event.provider, event.native_model)
    )
    statement = apply_ledger_filters(statement, filters)
    counts = {
        (row.provider, row.native_model): int(row.n)
        for row in await session.execute(statement)
    }
    total = sum(counts.values())
    severity = _severity_for(float(total), warn, crit)
    if severity is None:
        return None
    crossed = crit if severity == "critical" else warn
    return AlertFinding(
        severity=severity,
        title="Unknown model",
        body=(
            f"{total} events in the last {_ACCOUNTING_WINDOW_DAYS}d used a model the "
            f"registry does not know (threshold {crossed:.0f})."
        ),
        context={
            "unknown_model_events": total,
            "top_offenders": _top_offenders(counts),
            "open_data_quality_events": await _open_dq_count(session, "unknown_model"),
            "scoped_dimensions": filters.scoped_dimensions(),
        },
    )


def _aware(moment: datetime, reference: datetime) -> datetime:
    """Align a possibly-naive stored timestamp with ``reference`` for subtraction.

    SQLite reads timestamps back naive; assume UTC (how they are written) so
    staleness durations are correct on both SQLite and Postgres.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=reference.tzinfo or UTC)


def _stale_thresholds_minutes(
    rule: models.AlertRule, threshold_seconds: float
) -> tuple[float, float]:
    """Resolve a source's (warn, crit) staleness thresholds in minutes.

    ``warn`` falls back to the source-type staleness threshold from Task 63.2
    (``threshold_seconds``); ``crit`` falls back to a fixed multiple of warn.
    Explicit per-rule ``warn_threshold``/``crit_threshold`` (or the legacy single
    ``threshold``) override the defaults and apply to every scoped source.
    """
    warn = _threshold(rule, threshold_seconds / 60.0)
    crit = (
        float(rule.crit_threshold)
        if rule.crit_threshold is not None
        else warn * _STALE_CRIT_MULTIPLE
    )
    return warn, max(warn, crit)


async def evaluate_stale_sources(
    session: AsyncSession,
    rule: models.AlertRule,
    now: datetime | None = None,
    *,
    stale_thresholds: dict[str, float] | None = None,
    default_stale_seconds: float = DEFAULT_STALE_SECONDS,
) -> list[EntityFinding]:
    """Return one finding per stale, non-revoked, in-scope reporting source.

    A source is stale when the time since its last successful ingest (or its
    first sighting, if it never ingested successfully) crosses the warn or crit
    threshold. Thresholds default per source type (Task 63.2) but a rule may set
    explicit warn/crit minutes. Revoked sources never fire (FR-SOURCE-012). The
    rule's ``source`` dimension filter, when set, restricts the scan to those
    source names -- the other dimensions have no attribute on a source row and
    are ignored. Each finding carries the source identity and staleness duration;
    the filter scope is recorded content-free as dimension names only.
    """
    reference = now if now is not None else datetime.now(UTC)
    filters = filters_from_config(rule.config)
    health = SourceHealthService(
        session,
        stale_thresholds=stale_thresholds,
        default_stale_seconds=default_stale_seconds,
    )
    scoped = filters.scoped_dimensions()

    statement = select(models.Source).where(models.Source.revoked.is_(False))
    if filters.source:
        statement = statement.where(models.Source.name.in_(filters.source))
    sources = (await session.execute(statement)).scalars().all()

    findings: list[EntityFinding] = []
    for source in sources:
        threshold_seconds = health.staleness_threshold(source.type)
        warn, crit = _stale_thresholds_minutes(rule, threshold_seconds)
        baseline = source.last_successful_ingest or source.first_seen
        stale_minutes = (reference - _aware(baseline, reference)).total_seconds() / 60.0
        severity = _severity_for(stale_minutes, warn, crit)
        if severity is None:
            continue
        crossed = crit if severity == "critical" else warn
        last = source.last_successful_ingest
        findings.append(
            EntityFinding(
                key=str(source.id),
                finding=AlertFinding(
                    severity=severity,
                    title=f"Source stale: {source.name}",
                    body=(
                        f"Source '{source.name}' ({source.type}) has not ingested "
                        f"for {stale_minutes:.0f} min (threshold {crossed:.0f})."
                    ),
                    context={
                        "source_id": source.id,
                        "source_type": source.type,
                        "source_name": source.name,
                        "last_successful_ingest": (
                            _aware(last, reference).isoformat() if last is not None else None
                        ),
                        "stale_minutes": round(stale_minutes, 1),
                        "scoped_dimensions": scoped,
                    },
                ),
            )
        )
    return findings


#: Rule kind to single-finding evaluator.
EVALUATORS = {
    "limit_pct": _limit_pct,
    "predicted_exhaustion": _predicted_exhaustion,
    "burn_rate": _burn_rate,
    "collector_stale": _collector_stale,
    "unpriced_events": _unpriced_events,
    "unknown_model": _unknown_model,
}

#: Rule kinds that fire one finding per entity, tracked with per-entity state.
GROUPED_EVALUATORS = {
    "stale_source": evaluate_stale_sources,
}

#: Every valid rule kind (single-finding and grouped), for API validation.
ALL_EVALUATOR_KINDS = frozenset(EVALUATORS) | frozenset(GROUPED_EVALUATORS)


def is_grouped_kind(kind: str) -> bool:
    """Whether a rule kind fires one finding per entity (grouped state machine)."""
    return kind in GROUPED_EVALUATORS


async def evaluate_rule(
    session: AsyncSession, rule: models.AlertRule, now: datetime | None = None
) -> AlertFinding | None:
    """Evaluate a single-finding rule; return a finding or None.

    Grouped kinds (see :data:`GROUPED_EVALUATORS`) are evaluated by their own
    entrypoint (e.g. :func:`evaluate_stale_sources`), not here.

    Raises:
        ValueError: If the rule's kind has no single-finding evaluator.
    """
    evaluator = EVALUATORS.get(rule.kind)
    if evaluator is None:
        raise ValueError(f"unknown alert rule kind: {rule.kind}")
    reference = now if now is not None else datetime.now(UTC)
    return await evaluator(session, rule, reference)
