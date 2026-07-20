"""Alert rule evaluators.

Each rule ``kind`` maps to an async evaluator that inspects current state and
returns an :class:`AlertFinding` when the condition is met, or None. Rules are
data (rows in ``alert_rules``); this module is the logic they select.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import analytics
from tokemetry_server.services.alerting.filters import (
    AlertFilters,
    apply_ledger_filters,
    filters_from_config,
)

#: The token counters a burn-rate window sums (reasoning is excluded, matching
#: analytics.token_burn_rate).
_BURN_RATE_WINDOW_MINUTES = 60


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


async def _unknown_model(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when recent events could not be priced (unknown model)."""
    since = now - timedelta(days=1)
    filters = filters_from_config(rule.config)
    event = models.UsageEventV2
    statement = (
        select(func.count())
        .select_from(event)
        .where(
            event.event_kind == "attempt",
            event.finality == "final",
            event.ts_started >= since,
            event.cost_usd.is_(None),
        )
    )
    statement = apply_ledger_filters(statement, filters)
    count = int((await session.execute(statement)).scalar_one())
    if count > 0:
        return AlertFinding(
            severity="warning",
            title="Unpriced usage",
            body=f"{count} events in the last day have no known price (new model?).",
            context={
                "unpriced_events": count,
                "scoped_dimensions": filters.scoped_dimensions(),
            },
        )
    return None


#: Rule kind to evaluator.
EVALUATORS = {
    "limit_pct": _limit_pct,
    "predicted_exhaustion": _predicted_exhaustion,
    "burn_rate": _burn_rate,
    "collector_stale": _collector_stale,
    "unknown_model": _unknown_model,
}


async def evaluate_rule(
    session: AsyncSession, rule: models.AlertRule, now: datetime | None = None
) -> AlertFinding | None:
    """Evaluate a single rule; return a finding or None.

    Raises:
        ValueError: If the rule's kind has no evaluator.
    """
    evaluator = EVALUATORS.get(rule.kind)
    if evaluator is None:
        raise ValueError(f"unknown alert rule kind: {rule.kind}")
    reference = now if now is not None else datetime.now(UTC)
    return await evaluator(session, rule, reference)
