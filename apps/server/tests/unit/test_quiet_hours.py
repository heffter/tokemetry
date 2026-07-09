"""Unit tests for timezone-aware alert quiet hours."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tokemetry_server.db import models
from tokemetry_server.services.alerting.engine import _in_quiet_hours, _resolve_zone


def _rule(start: int, end: int) -> models.AlertRule:
    return models.AlertRule(
        name="q",
        kind="limit_pct",
        quiet_hours={"start_hour": start, "end_hour": end},
    )


def test_resolve_zone_known() -> None:
    assert _resolve_zone("Europe/Budapest").key == "Europe/Budapest"  # type: ignore[attr-defined]


def test_resolve_zone_unknown_falls_back_to_utc() -> None:
    assert _resolve_zone("Not/AZone") is UTC


def test_quiet_hours_evaluated_in_user_timezone() -> None:
    rule = _rule(22, 7)  # 22:00-07:00 night window that wraps midnight
    # 21:30 UTC is 22:30 in Budapest (winter, UTC+1): quiet locally, not in UTC.
    now = datetime(2026, 1, 10, 21, 30, tzinfo=UTC)
    assert _in_quiet_hours(rule, now, ZoneInfo("Europe/Budapest")) is True
    assert _in_quiet_hours(rule, now, UTC) is False


def test_missing_quiet_hours_config_is_never_quiet() -> None:
    rule = models.AlertRule(name="q", kind="limit_pct", quiet_hours=None)
    assert _in_quiet_hours(rule, datetime(2026, 1, 10, 3, 0, tzinfo=UTC)) is False
