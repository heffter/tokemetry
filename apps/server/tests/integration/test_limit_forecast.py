"""Per-stream limit forecasting (Task 69.6, FR-LIMIT-008)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tokemetry_server.db import models
from tokemetry_server.services.limit_forecast import (
    forecast_stream,
    forecast_streams,
)

_T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _snap(
    minutes: float,
    util: float,
    *,
    account: str | None = None,
    window_kind: str = "five_hour",
    provider: str = "anthropic",
    resets_at: datetime | None = None,
) -> models.LimitSnapshot:
    return models.LimitSnapshot(
        provider=provider,
        machine=None,
        ts=_T0 + timedelta(minutes=minutes),
        window_kind=window_kind,
        utilization_pct=util,
        resets_at=resets_at,
        provenance="official",
        account=account,
        organization=None,
        source_id=None,
        limit_amount=None,
        remaining=None,
        unit=None,
        raw={},
    )


def _series(
    count: int, span_minutes: float, start: float, end: float
) -> list[models.LimitSnapshot]:
    step = span_minutes / (count - 1)
    util_step = (end - start) / (count - 1)
    return [_snap(i * step, start + i * util_step) for i in range(count)]


def test_forecasts_exhaustion_from_slope() -> None:
    # 20% -> 60% over 40 minutes = 1%/min; 40% left -> 40 min to 100%.
    forecast = forecast_stream([_snap(0, 20.0), _snap(40, 60.0)])
    assert forecast.utilization_pct == 60.0
    assert forecast.slope_pct_per_min == 1.0
    assert forecast.predicted_exhaustion_at == _T0 + timedelta(minutes=40 + 40)
    assert forecast.stream.provider == "anthropic"
    assert forecast.stream.window_kind == "five_hour"


def test_flat_or_falling_usage_never_predicts_exhaustion() -> None:
    assert forecast_stream([_snap(0, 50.0), _snap(30, 50.0)]).predicted_exhaustion_at is None
    assert forecast_stream([_snap(0, 80.0), _snap(30, 40.0)]).predicted_exhaustion_at is None


def test_insufficient_data_is_unavailable_not_a_guess() -> None:
    forecast = forecast_stream([_snap(0, 42.0)])
    assert forecast.confidence == "unavailable"
    assert forecast.predicted_exhaustion_at is None
    assert forecast.sample_count == 1


def test_confidence_scales_with_samples_and_span() -> None:
    assert forecast_stream(_series(2, 5, 10, 20)).confidence == "low"
    assert forecast_stream(_series(5, 30, 10, 40)).confidence == "medium"
    assert forecast_stream(_series(12, 90, 10, 70)).confidence == "high"


def test_period_reset_before_exhaustion_is_flagged() -> None:
    # 20% -> 60% over 40 min (1%/min) predicts 100% at 13:20 (+40 from the last
    # reading at 12:40). A reset at 13:10 arrives first, so it will reset before
    # exhausting (calendar/rolling reset semantics).
    resets = _T0 + timedelta(minutes=70)  # 13:10, before the 13:20 prediction
    forecast = forecast_stream(
        [_snap(0, 20.0), _snap(40, 60.0, resets_at=resets)]
    )
    assert forecast.predicted_exhaustion_at == _T0 + timedelta(minutes=80)
    assert forecast.will_reset_first is True


def test_each_account_forecasts_independently() -> None:
    # FR-LIMIT-005: two accounts on the same window are two independent forecasts.
    snapshots = [
        _snap(0, 10.0, account="acct-a"),
        _snap(40, 90.0, account="acct-a"),
        _snap(0, 10.0, account="acct-b"),
        _snap(40, 20.0, account="acct-b"),
    ]
    forecasts = {f.stream.account: f for f in forecast_streams(snapshots)}
    assert set(forecasts) == {"acct-a", "acct-b"}
    # Steep account-a predicts exhaustion; shallow account-b does not this soon.
    assert forecasts["acct-a"].slope_pct_per_min > forecasts["acct-b"].slope_pct_per_min


def test_anthropic_five_hour_regression_matches_slope_math() -> None:
    # The generalized forecast reproduces the existing burn-rate math on the
    # Anthropic five-hour window (identical fixtures).
    snapshots = [_snap(0, 30.0), _snap(60, 75.0)]
    forecast = forecast_stream(snapshots)
    # slope = (75-30)/60 = 0.75%/min; 25% left / 0.75 = 33.33 min from last (+60).
    assert forecast.slope_pct_per_min == (75.0 - 30.0) / 60.0
    minutes_left = (100.0 - 75.0) / forecast.slope_pct_per_min
    assert forecast.predicted_exhaustion_at == _T0 + timedelta(
        minutes=60 + minutes_left
    )
