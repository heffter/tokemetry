"""Per-stream limit forecasting with source and confidence labeling.

Generalizes the Anthropic five-hour burn-rate prediction (services/analytics)
to any provider limit stream (FR-LIMIT-008). Each forecast:

- identifies its **source data** -- the limit stream it was computed from
  (provider, window kind, account, organization, source), so a consumer knows
  exactly which series drove it (FR-LIMIT-005: each stream forecasts
  independently, so two accounts never blur together);
- carries a **confidence** derived from sample density and the span of the
  readings, and yields ``unavailable`` rather than a guess when there is too
  little history (FR-DIM-010 semantics);
- is **period-aware**: when the window resets before the extrapolated
  exhaustion, ``will_reset_first`` flags that the limit will not be hit this
  period, which matters for both rolling and calendar windows.

Pure and unit-tested; it operates on already-grouped snapshots so it needs no
database access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from tokemetry_server.db import models
from tokemetry_server.services.limit_grouping import (
    LimitStreamKey,
    group_limit_snapshots,
    stream_key,
)

Confidence = Literal["high", "medium", "low", "unavailable"]

# Confidence thresholds: (min samples, min span minutes) for each tier.
_HIGH_SAMPLES, _HIGH_SPAN_MIN = 8, 45.0
_MEDIUM_SAMPLES, _MEDIUM_SPAN_MIN = 4, 15.0


@dataclass(frozen=True)
class LimitForecast:
    """An exhaustion forecast for one limit stream (FR-LIMIT-008)."""

    stream: LimitStreamKey
    utilization_pct: float
    slope_pct_per_min: float
    predicted_exhaustion_at: datetime | None
    resets_at: datetime | None
    #: True when the window resets before the extrapolated exhaustion, so the
    #: limit will not actually be reached this period.
    will_reset_first: bool
    sample_count: int
    confidence: Confidence


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _confidence(sample_count: int, span_minutes: float) -> Confidence:
    """Confidence from sample density and the span of the readings."""
    if sample_count < 2 or span_minutes <= 0:
        return "unavailable"
    if sample_count >= _HIGH_SAMPLES and span_minutes >= _HIGH_SPAN_MIN:
        return "high"
    if sample_count >= _MEDIUM_SAMPLES and span_minutes >= _MEDIUM_SPAN_MIN:
        return "medium"
    return "low"


def forecast_stream(snapshots: list[models.LimitSnapshot]) -> LimitForecast:
    """Forecast one stream's exhaustion from its recent utilization slope.

    ``snapshots`` must be non-empty and all belong to one stream. Fewer than two
    readings (or a zero time span) yields ``unavailable`` with no prediction.
    """
    if not snapshots:
        raise ValueError("forecast_stream requires at least one snapshot")
    ordered = sorted(snapshots, key=lambda s: _as_utc(s.ts))
    key = stream_key(ordered[0])
    last = ordered[-1]
    utilization = float(last.utilization_pct)
    resets_at = _as_utc(last.resets_at) if last.resets_at is not None else None
    span_minutes = (
        _as_utc(last.ts) - _as_utc(ordered[0].ts)
    ).total_seconds() / 60.0
    confidence = _confidence(len(ordered), span_minutes)

    if confidence == "unavailable":
        return LimitForecast(
            stream=key,
            utilization_pct=utilization,
            slope_pct_per_min=0.0,
            predicted_exhaustion_at=None,
            resets_at=resets_at,
            will_reset_first=False,
            sample_count=len(ordered),
            confidence="unavailable",
        )

    first = ordered[0]
    slope = (utilization - float(first.utilization_pct)) / span_minutes
    predicted: datetime | None = None
    if slope > 0 and utilization < 100:
        minutes_left = (100.0 - utilization) / slope
        predicted = _as_utc(last.ts) + timedelta(minutes=minutes_left)
    will_reset_first = (
        predicted is not None and resets_at is not None and resets_at <= predicted
    )
    return LimitForecast(
        stream=key,
        utilization_pct=utilization,
        slope_pct_per_min=slope,
        predicted_exhaustion_at=predicted,
        resets_at=resets_at,
        will_reset_first=will_reset_first,
        sample_count=len(ordered),
        confidence=confidence,
    )


def forecast_streams(
    snapshots: list[models.LimitSnapshot],
) -> list[LimitForecast]:
    """Group snapshots into streams and forecast each independently.

    Each distinct (provider, window_kind, account, organization, source_id) is
    forecast on its own series (FR-LIMIT-005), so multi-account or gateway +
    collector streams never merge.
    """
    return [
        forecast_stream(stream_snapshots)
        for stream_snapshots in group_limit_snapshots(snapshots).values()
    ]
