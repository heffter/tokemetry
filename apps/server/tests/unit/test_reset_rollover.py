"""Unit tests for stale limit-reset rollover."""

from datetime import UTC, datetime, timedelta

from tokemetry_server.services.analytics import roll_reset_forward

_NOW = datetime(2026, 7, 9, 19, 0, tzinfo=UTC)


def test_future_reset_passes_through() -> None:
    future = _NOW + timedelta(hours=2)
    rolled, derived = roll_reset_forward("five_hour", future, _NOW)
    assert rolled == future
    assert derived is False


def test_past_five_hour_rolls_one_step() -> None:
    past = _NOW - timedelta(hours=2)
    rolled, derived = roll_reset_forward("five_hour", past, _NOW)
    assert derived is True
    assert rolled == past + timedelta(hours=5)
    assert rolled is not None and rolled > _NOW


def test_multiple_steps_until_future() -> None:
    past = _NOW - timedelta(hours=12)
    rolled, derived = roll_reset_forward("five_hour", past, _NOW)
    assert derived is True
    assert rolled == past + timedelta(hours=15)
    assert rolled is not None and rolled > _NOW


def test_weekly_rolls_in_seven_day_steps() -> None:
    past = _NOW - timedelta(days=2)
    rolled, derived = roll_reset_forward("seven_day", past, _NOW)
    assert derived is True
    assert rolled == past + timedelta(days=7)


def test_naive_reset_treated_as_utc() -> None:
    naive = (_NOW - timedelta(hours=1)).replace(tzinfo=None)
    rolled, derived = roll_reset_forward("five_hour", naive, _NOW)
    assert derived is True
    assert rolled is not None and rolled.tzinfo is not None


def test_none_reset() -> None:
    assert roll_reset_forward("five_hour", None, _NOW) == (None, False)


def test_unknown_window_passes_through() -> None:
    past = _NOW - timedelta(hours=1)
    rolled, derived = roll_reset_forward("mystery", past, _NOW)
    assert derived is False
    assert rolled == past
