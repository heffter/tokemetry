"""Statistical anomaly detection over sessions.

Flags sessions that deviate from the account's *own* baseline rather than any
absolute threshold: unusually large, expensive, or poorly-cached sessions, and
a composite "cost-inefficient" case (above-average cost with below-average
cache reuse). Anomalies rank by ``cost * (1 - cache_hit_rate)`` so the
expensive-and-poorly-cached sessions surface first. Metadata only.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.projects import DEFAULT_ROOTS, project_group

from tokemetry_server.db import models

#: Minimum sessions before a baseline is statistically meaningful.
MIN_SESSIONS = 20

#: Sessions below this token count are too small to flag on size/cache.
MIN_TOKEN_FLOOR = 50_000

#: Standard-deviation multiple that defines an outlier.
_SIGMA = 2.0


@dataclass(frozen=True)
class SessionAgg:
    """Per-session aggregate used as anomaly-detector input."""

    session_id: str
    project: str | None
    total_tokens: int
    cost_usd: float | None
    cache_read_tokens: int
    input_tokens: int

    @property
    def cache_hit_rate(self) -> float:
        """Cache-read share of prompt tokens (read / (read + input))."""
        prompt = self.cache_read_tokens + self.input_tokens
        return self.cache_read_tokens / prompt if prompt else 0.0


@dataclass(frozen=True)
class Anomaly:
    """A flagged session with the reasons it stood out."""

    session_id: str
    project: str | None
    reasons: list[str]
    severity_score: float
    total_tokens: int
    cost_usd: float | None
    cache_hit_rate: float


@dataclass(frozen=True)
class AnomalyReport:
    """Anomaly-detection result over the account's sessions."""

    enough_data: bool
    session_count: int
    anomalies: list[Anomaly] = field(default_factory=list)


def classify_anomalies(
    sessions: Sequence[SessionAgg],
    min_sessions: int = MIN_SESSIONS,
    floor: int = MIN_TOKEN_FLOOR,
) -> AnomalyReport:
    """Flag outlier sessions against the population's own mean +/- 2 sigma.

    Returns an empty report with ``enough_data=False`` when there are fewer
    than ``min_sessions`` sessions to form a baseline.
    """
    if len(sessions) < min_sessions:
        return AnomalyReport(enough_data=False, session_count=len(sessions))

    tokens = [s.total_tokens for s in sessions]
    costs = [s.cost_usd for s in sessions if s.cost_usd is not None]
    hit_rates = [s.cache_hit_rate for s in sessions]

    mean_tokens = statistics.fmean(tokens)
    std_tokens = statistics.pstdev(tokens)
    mean_cost = statistics.fmean(costs) if costs else 0.0
    std_cost = statistics.pstdev(costs) if len(costs) > 1 else 0.0
    mean_hit = statistics.fmean(hit_rates)
    std_hit = statistics.pstdev(hit_rates)

    anomalies: list[Anomaly] = []
    for agg in sessions:
        reasons: list[str] = []
        big_enough = agg.total_tokens >= floor
        if big_enough and std_tokens > 0 and agg.total_tokens > mean_tokens + _SIGMA * std_tokens:
            reasons.append("high tokens")
        if (
            agg.cost_usd is not None
            and std_cost > 0
            and agg.cost_usd > mean_cost + _SIGMA * std_cost
        ):
            reasons.append("high cost")
        if big_enough and std_hit > 0 and agg.cache_hit_rate < mean_hit - _SIGMA * std_hit:
            reasons.append("low cache reuse")
        if (
            agg.cost_usd is not None
            and agg.cost_usd > mean_cost
            and agg.cache_hit_rate < mean_hit
            and big_enough
        ):
            reasons.append("cost-inefficient")

        if reasons:
            severity = (agg.cost_usd or 0.0) * (1.0 - agg.cache_hit_rate)
            anomalies.append(
                Anomaly(
                    session_id=agg.session_id,
                    project=agg.project,
                    reasons=reasons,
                    severity_score=severity,
                    total_tokens=agg.total_tokens,
                    cost_usd=agg.cost_usd,
                    cache_hit_rate=agg.cache_hit_rate,
                )
            )

    anomalies.sort(key=lambda a: a.severity_score, reverse=True)
    return AnomalyReport(
        enough_data=True, session_count=len(sessions), anomalies=anomalies
    )


async def detect_anomalies(
    session: AsyncSession, roots: Sequence[str] = DEFAULT_ROOTS
) -> AnomalyReport:
    """Aggregate per-session usage and classify anomalies against the baseline."""
    event = models.UsageEvent
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    rows = (
        await session.execute(
            select(
                event.session_id,
                func.min(event.project),
                func.coalesce(func.sum(event.input_tokens), 0),
                func.coalesce(func.sum(event.cache_read_tokens), 0),
                func.coalesce(func.sum(total), 0),
                func.sum(event.cost_usd),
            )
            .where(event.session_id.is_not(None))
            .group_by(event.session_id)
        )
    ).all()

    aggs = [
        SessionAgg(
            session_id=str(row[0]),
            project=project_group(row[1], roots),
            input_tokens=int(row[2] or 0),
            cache_read_tokens=int(row[3] or 0),
            total_tokens=int(row[4] or 0),
            cost_usd=None if row[5] is None else float(Decimal(str(row[5]))),
        )
        for row in rows
    ]
    return classify_anomalies(aggs)
