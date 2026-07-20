"""Alert rule evaluators.

Each rule ``kind`` maps to an async evaluator that inspects current state and
returns an :class:`AlertFinding` when the condition is met, or None. Rules are
data (rows in ``alert_rules``); this module is the logic they select.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
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

#: Default sliding window (minutes) for the reliability kinds (failure_rate,
#: latency_p95, fallback_rate) when a rule sets no ``window_minutes``.
_RELIABILITY_WINDOW_MINUTES = 60

#: Default minimum sample size below which a reliability kind stays silent, so a
#: single failure in a tiny window does not fire. Overridable via ``min_samples``.
_RELIABILITY_MIN_SAMPLES = 20


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


#: Schema versions the v2 ingest path accepts (mirrors the ``schema_version == 2``
#: check in api/v2/ingest.py); a source reporting anything else has drifted.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({2})


async def evaluate_schema_drift(
    session: AsyncSession,
    rule: models.AlertRule,
    now: datetime | None = None,
    *,
    supported_versions: frozenset[int] = SUPPORTED_SCHEMA_VERSIONS,
) -> list[EntityFinding]:
    """Return one finding per non-revoked source that has drifted (FR-ALERT-006).

    A source drifts when the batch ``schema_version`` it last reported is outside
    the server-supported set (a proxy or collector upgraded ahead of, or lagging
    behind, the server), or when its rolling validation-rejection count (Task
    63.2 source health) crosses the warn/crit thresholds. A version mismatch is
    always critical; rejection volume derives its severity from the thresholds.
    Honors the rule's ``source`` name filter; revoked sources never fire. Context
    carries source identity, reported versus supported versions, and the rejection
    count, and links the open ``schema_drift`` data-quality count.

    ``now`` is unused (drift is read from stored source-health state, not a time
    window) but is part of the grouped-evaluator signature the engine calls.
    """
    del now
    filters = filters_from_config(rule.config)
    warn, crit = _warn_crit(rule, 5.0, 20.0)
    scoped = filters.scoped_dimensions()
    supported = sorted(supported_versions)
    open_drift = await _open_dq_count(session, "schema_drift")

    statement = select(models.Source).where(models.Source.revoked.is_(False))
    if filters.source:
        statement = statement.where(models.Source.name.in_(filters.source))
    sources = (await session.execute(statement)).scalars().all()

    findings: list[EntityFinding] = []
    for source in sources:
        version = source.reported_schema_version
        rejections = source.recent_error_count or 0
        version_drift = version is not None and version not in supported_versions
        rejection_severity = _severity_for(float(rejections), warn, crit)
        if version_drift:
            severity = "critical"
        elif rejection_severity is not None:
            severity = rejection_severity
        else:
            continue
        reasons: list[str] = []
        if version_drift:
            reasons.append(f"reports schema_version {version} (supported: {supported})")
        if rejection_severity is not None:
            reasons.append(f"{rejections} recent validation rejections")
        findings.append(
            EntityFinding(
                key=str(source.id),
                finding=AlertFinding(
                    severity=severity,
                    title=f"Schema drift: {source.name}",
                    body=f"Source '{source.name}' ({source.type}) {'; '.join(reasons)}.",
                    context={
                        "source_id": source.id,
                        "source_type": source.type,
                        "source_name": source.name,
                        "reported_schema_version": version,
                        "supported_schema_versions": supported,
                        "version_drift": version_drift,
                        "recent_error_count": rejections,
                        "open_data_quality_events": open_drift,
                        "scoped_dimensions": scoped,
                    },
                ),
            )
        )
    return findings


def _config_int(rule: models.AlertRule, key: str, default: int) -> int:
    """Read a positive integer from a rule's config, or a default.

    The API validates ``window_minutes``/``min_samples`` as ``>= 1``; this guards
    against hand-written rows by falling back to the default on anything invalid.
    """
    raw = (rule.config or {}).get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return default
    return raw


def _percentile(values: list[int], pct: float) -> float:
    """Nearest-rank percentile of ``values`` (assumed non-empty), 0 <= pct <= 100."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


async def _failure_rate(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when the failed-attempt share crosses a warn/crit percentage (FR-ALERT-007).

    Over final attempts in the sliding window, ``failed / total`` as a percent.
    Stays silent below the minimum sample size so a tiny window cannot fire.
    """
    warn, crit = _warn_crit(rule, 10.0, 25.0)
    filters = filters_from_config(rule.config)
    window = _config_int(rule, "window_minutes", _RELIABILITY_WINDOW_MINUTES)
    min_samples = _config_int(rule, "min_samples", _RELIABILITY_MIN_SAMPLES)
    since = now - timedelta(minutes=window)
    event = models.UsageEventV2
    statement = select(
        func.count().label("total"),
        func.sum(case((event.success.is_(False), 1), else_=0)).label("failed"),
    ).where(
        event.event_kind == "attempt",
        event.finality == "final",
        event.ts_started >= since,
    )
    statement = apply_ledger_filters(statement, filters)
    row = (await session.execute(statement)).one()
    total = int(row.total or 0)
    if total < min_samples:
        return None
    failed = int(row.failed or 0)
    pct = 100.0 * failed / total
    severity = _severity_for(pct, warn, crit)
    if severity is None:
        return None
    crossed = crit if severity == "critical" else warn
    return AlertFinding(
        severity=severity,
        title="High failure rate",
        body=(
            f"{pct:.1f}% of {total} attempts in the last {window}m failed "
            f"(threshold {crossed:.0f}%)."
        ),
        context={
            "failure_rate_pct": round(pct, 2),
            "failed": failed,
            "sample_size": total,
            "window_minutes": window,
            "scoped_dimensions": filters.scoped_dimensions(),
        },
    )


async def _latency_p95(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when the p95 of ``latency_ms`` crosses a warn/crit threshold (FR-ALERT-007).

    Percentile is over final attempts in the window that recorded a latency;
    stays silent below the minimum sample size.
    """
    warn, crit = _warn_crit(rule, 10_000.0, 30_000.0)
    filters = filters_from_config(rule.config)
    window = _config_int(rule, "window_minutes", _RELIABILITY_WINDOW_MINUTES)
    min_samples = _config_int(rule, "min_samples", _RELIABILITY_MIN_SAMPLES)
    since = now - timedelta(minutes=window)
    event = models.UsageEventV2
    statement = select(event.latency_ms).where(
        event.event_kind == "attempt",
        event.finality == "final",
        event.ts_started >= since,
        event.latency_ms.is_not(None),
    )
    statement = apply_ledger_filters(statement, filters)
    values = [int(v) for (v,) in await session.execute(statement)]
    if len(values) < min_samples:
        return None
    p95 = _percentile(values, 95.0)
    severity = _severity_for(p95, warn, crit)
    if severity is None:
        return None
    crossed = crit if severity == "critical" else warn
    return AlertFinding(
        severity=severity,
        title="High latency (p95)",
        body=(
            f"p95 latency {p95:.0f}ms over {len(values)} attempts in the last "
            f"{window}m (threshold {crossed:.0f}ms)."
        ),
        context={
            "latency_p95_ms": p95,
            "sample_size": len(values),
            "window_minutes": window,
            "scoped_dimensions": filters.scoped_dimensions(),
        },
    )


async def _fallback_rate(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when the share of logical requests that fell back crosses a threshold.

    The denominator is *logical requests* (not attempts) so a multi-attempt
    fallback is counted once (FR-ALERT-008). Only the provider and model
    dimensions apply -- a logical request carries no source/project/environment
    -- so those filters are recorded as scope, the rest ignored.
    """
    warn, crit = _warn_crit(rule, 10.0, 25.0)
    filters = filters_from_config(rule.config)
    window = _config_int(rule, "window_minutes", _RELIABILITY_WINDOW_MINUTES)
    min_samples = _config_int(rule, "min_samples", _RELIABILITY_MIN_SAMPLES)
    since = now - timedelta(minutes=window)
    lr = models.LogicalRequest
    statement = select(
        func.count().label("total"),
        func.sum(case((lr.fallback_count > 0, 1), else_=0)).label("fell_back"),
    ).where(lr.ts_last.is_not(None), lr.ts_last >= since)
    applied: list[str] = []
    if filters.provider:
        statement = statement.where(lr.provider.in_(filters.provider))
        applied.append("provider")
    if filters.model:
        statement = statement.where(lr.requested_model.in_(filters.model))
        applied.append("model")
    row = (await session.execute(statement)).one()
    total = int(row.total or 0)
    if total < min_samples:
        return None
    fell_back = int(row.fell_back or 0)
    pct = 100.0 * fell_back / total
    severity = _severity_for(pct, warn, crit)
    if severity is None:
        return None
    crossed = crit if severity == "critical" else warn
    return AlertFinding(
        severity=severity,
        title="High fallback rate",
        body=(
            f"{pct:.1f}% of {total} logical requests in the last {window}m fell "
            f"back (threshold {crossed:.0f}%)."
        ),
        context={
            "fallback_rate_pct": round(pct, 2),
            "fell_back": fell_back,
            "sample_size": total,
            "window_minutes": window,
            "scoped_dimensions": applied,
        },
    )


#: Rule kind to single-finding evaluator.
EVALUATORS = {
    "limit_pct": _limit_pct,
    "predicted_exhaustion": _predicted_exhaustion,
    "burn_rate": _burn_rate,
    "collector_stale": _collector_stale,
    "unpriced_events": _unpriced_events,
    "unknown_model": _unknown_model,
    "failure_rate": _failure_rate,
    "latency_p95": _latency_p95,
    "fallback_rate": _fallback_rate,
}

#: Rule kinds that fire one finding per entity, tracked with per-entity state.
#: These are dispatched by the engine to their own per-entity entrypoints
#: (``evaluate_stale_sources``, ``evaluate_schema_drift``), never through
#: :data:`EVALUATORS`.
GROUPED_EVALUATOR_KINDS = frozenset({"stale_source", "schema_drift"})

#: Every valid rule kind (single-finding and grouped), for API validation.
ALL_EVALUATOR_KINDS = frozenset(EVALUATORS) | GROUPED_EVALUATOR_KINDS


def is_grouped_kind(kind: str) -> bool:
    """Whether a rule kind fires one finding per entity (grouped state machine)."""
    return kind in GROUPED_EVALUATOR_KINDS


async def evaluate_rule(
    session: AsyncSession, rule: models.AlertRule, now: datetime | None = None
) -> AlertFinding | None:
    """Evaluate a single-finding rule; return a finding or None.

    Grouped kinds (see :data:`GROUPED_EVALUATOR_KINDS`) are evaluated by their
    own entrypoint (e.g. :func:`evaluate_stale_sources`), not here.

    Raises:
        ValueError: If the rule's kind has no single-finding evaluator.
    """
    evaluator = EVALUATORS.get(rule.kind)
    if evaluator is None:
        raise ValueError(f"unknown alert rule kind: {rule.kind}")
    reference = now if now is not None else datetime.now(UTC)
    return await evaluator(session, rule, reference)
