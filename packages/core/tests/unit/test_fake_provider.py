"""Unit tests for the fake provider reference implementation."""

from datetime import date
from decimal import Decimal

import pytest
from tokemetry_core.interfaces import (
    LimitsSource,
    LimitsUnavailableError,
    PricingStrategy,
    UsageSource,
)
from tokemetry_core.models import PriceRow, Provenance
from tokemetry_core.providers.fake import (
    FakeLimitsSource,
    FakePricingStrategy,
    FakeUsageSource,
)


def test_fake_source_satisfies_interface() -> None:
    source: UsageSource = FakeUsageSource()
    assert isinstance(source, UsageSource)
    assert isinstance(FakeLimitsSource(), LimitsSource)
    assert isinstance(FakePricingStrategy(), PricingStrategy)


def test_discover_reports_sized_files() -> None:
    source = FakeUsageSource(files=3, events_per_file=2)
    files = source.discover()
    assert len(files) == 3
    assert all(file.size == 200 for file in files)


def test_parse_from_zero_emits_all_events() -> None:
    source = FakeUsageSource(events_per_file=3)
    file = source.discover()[0]

    result = source.parse(file, offset=0)

    assert len(result.events) == 3
    assert result.new_offset == 300
    assert all(event.provider == "fake" for event in result.events)
    ids = [event.event_id for event in result.events]
    assert len(ids) == len(set(ids)), "event ids must be unique"


def test_parse_from_new_offset_emits_nothing() -> None:
    source = FakeUsageSource(events_per_file=3)
    file = source.discover()[0]
    first = source.parse(file, offset=0)

    second = source.parse(file, offset=first.new_offset)

    assert second.events == ()
    assert second.new_offset == first.new_offset


def test_parse_resumes_mid_file() -> None:
    source = FakeUsageSource(events_per_file=3)
    file = source.discover()[0]

    result = source.parse(file, offset=100)

    assert len(result.events) == 2


def test_bootstrap_returns_stats_cache_aggregate() -> None:
    aggregates = FakeUsageSource().bootstrap()
    assert len(aggregates) == 1
    assert aggregates[0].day == date(2025, 12, 31)
    assert aggregates[0].provenance is Provenance.STATS_CACHE


def test_limits_poll_returns_windows() -> None:
    snapshots = FakeLimitsSource().poll()
    kinds = {snapshot.window_kind for snapshot in snapshots}
    assert kinds == {"hourly", "weekly"}
    assert all(snapshot.provider == "fake" for snapshot in snapshots)


def test_limits_poll_failure_mode() -> None:
    with pytest.raises(LimitsUnavailableError):
        FakeLimitsSource(fail=True).poll()


def test_pricing_linear_math() -> None:
    source = FakeUsageSource(events_per_file=1)
    event = source.parse(source.discover()[0], offset=0).events[0]
    price = PriceRow(
        provider="fake",
        model="fake-model-1",
        effective_date=date(2026, 1, 1),
        input_per_mtok=Decimal("10"),
        output_per_mtok=Decimal("20"),
        cache_read_per_mtok=Decimal("1"),
        cache_write_short_per_mtok=Decimal("12.5"),
        cache_write_long_per_mtok=Decimal("20"),
    )

    cost = FakePricingStrategy().cost(event, price)

    # 100*10 + 50*20 + 1000*1 + 200*12.5 + 300*20 = 11500 per-MTok units
    assert cost == Decimal("0.0115")
