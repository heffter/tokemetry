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


@dataclass(frozen=True)
class AlertFinding:
    """A fired alert: what to say and how severe."""

    severity: str
    title: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


def _threshold(rule: models.AlertRule, default: float) -> float:
    """Read a rule's numeric threshold, or a default."""
    return float(rule.threshold) if rule.threshold is not None else default


async def _limit_pct(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when a limit window's utilization reaches the threshold."""
    threshold = _threshold(rule, 80.0)
    window = rule.window_kind or "five_hour"
    for snapshot in await analytics.current_limits(session):
        if snapshot.window_kind != window:
            continue
        pct = float(snapshot.utilization_pct)
        if pct >= threshold:
            severity = "critical" if pct >= 95 else "warning"
            return AlertFinding(
                severity=severity,
                title=f"{window} at {pct:.0f}%",
                body=f"Utilization {pct:.1f}% has reached the {threshold:.0f}% threshold.",
                context={"window_kind": window, "utilization_pct": pct},
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
    """Fire when the token burn rate exceeds an absolute threshold."""
    threshold = _threshold(rule, 5000.0)
    rate = await analytics.token_burn_rate(session, now=now)
    if rate >= threshold:
        return AlertFinding(
            severity="warning",
            title="High burn rate",
            body=f"Burning {rate:.0f} tokens/min (threshold {threshold:.0f}).",
            context={"burn_rate_per_min": rate},
        )
    return None


async def _collector_stale(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when a machine has not reported within the threshold minutes."""
    minutes = _threshold(rule, 30.0)
    cutoff = now - timedelta(minutes=minutes)
    result = await session.execute(
        select(models.Machine.id, models.Machine.last_seen).where(
            models.Machine.last_seen.is_not(None), models.Machine.last_seen < cutoff
        )
    )
    stale = [row[0] for row in result.all()]
    if stale:
        return AlertFinding(
            severity="serious",
            title="Collector stale",
            body=f"No data from {', '.join(stale)} for over {minutes:.0f} minutes.",
            context={"machines": stale},
        )
    return None


async def _unknown_model(
    session: AsyncSession, rule: models.AlertRule, now: datetime
) -> AlertFinding | None:
    """Fire when recent events could not be priced (unknown model)."""
    since = now - timedelta(days=1)
    result = await session.execute(
        select(func.count())
        .select_from(models.UsageEvent)
        .where(models.UsageEvent.ts >= since, models.UsageEvent.cost_usd.is_(None))
    )
    count = int(result.scalar_one())
    if count > 0:
        return AlertFinding(
            severity="warning",
            title="Unpriced usage",
            body=f"{count} events in the last day have no known price (new model?).",
            context={"unpriced_events": count},
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
