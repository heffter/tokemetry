"""Fake provider: deterministic test double proving the core interfaces.

Used only by test suites (of core, collector, and server) to exercise the
full pipeline without any real provider. Keeping it in the shipped package
(rather than test helpers) guarantees every consumer tests against the same
reference implementation and keeps provider-specific assumptions out of
core code paths.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tokemetry_core.interfaces import (
    LimitsSource,
    LimitsUnavailableError,
    PricingStrategy,
    UsageSource,
)
from tokemetry_core.models import (
    DailyAggregate,
    LimitSnapshot,
    ParseResult,
    PriceRow,
    Provenance,
    SourceFile,
    UsageEvent,
)
from tokemetry_core.registry import ProviderRegistry

FAKE_PROVIDER = "fake"

#: Synthetic byte width of one fake event; offsets advance in these steps so
#: offset semantics (resume, no re-emission) can be asserted in tests.
_EVENT_STRIDE = 100

#: Fixed base timestamp keeping fake data fully deterministic.
_BASE_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FakeUsageSource(UsageSource):
    """In-memory usage source emitting deterministic synthetic events."""

    provider = FAKE_PROVIDER

    def __init__(self, files: int = 1, events_per_file: int = 2) -> None:
        """Create a source pretending to own ``files`` artifacts.

        Args:
            files: Number of synthetic source files to report.
            events_per_file: Events contained in each synthetic file.
        """
        self._files = files
        self._events_per_file = events_per_file

    def discover(self) -> list[SourceFile]:
        """Report the synthetic files with their deterministic sizes."""
        size = self._events_per_file * _EVENT_STRIDE
        return [
            SourceFile(path=Path(f"fake-source-{index}.log"), size=size)
            for index in range(self._files)
        ]

    def parse(self, file: SourceFile, offset: int) -> ParseResult:
        """Emit the events located at or after byte ``offset``."""
        events = []
        for index in range(self._events_per_file):
            position = index * _EVENT_STRIDE
            if position < offset:
                continue
            events.append(
                UsageEvent(
                    event_id=f"{file.path.name}:{index}",
                    provider=self.provider,
                    native_model="fake-model-1",
                    ts=_BASE_TS + timedelta(minutes=index),
                    session_id=f"session-{file.path.name}",
                    project="fake-project",
                    input_tokens=100,
                    output_tokens=50,
                    cache_read_tokens=1000,
                    cache_write_short_tokens=200,
                    cache_write_long_tokens=300,
                    extra={"fake_counter": index},
                )
            )
        return ParseResult(
            events=tuple(events),
            new_offset=self._events_per_file * _EVENT_STRIDE,
        )

    def bootstrap(self) -> list[DailyAggregate]:
        """Return one synthetic historical day."""
        return [
            DailyAggregate(
                provider=self.provider,
                day=date(2025, 12, 31),
                native_model="fake-model-1",
                input_tokens=1000,
                output_tokens=500,
                message_count=10,
                provenance=Provenance.STATS_CACHE,
            )
        ]


class FakeLimitsSource(LimitsSource):
    """Limits source returning fixed utilization, with a failure mode."""

    provider = FAKE_PROVIDER

    def __init__(self, fail: bool = False) -> None:
        """Create the source.

        Args:
            fail: When true, :meth:`poll` raises
                :class:`LimitsUnavailableError` so degradation paths can be
                tested.
        """
        self._fail = fail

    def poll(self) -> list[LimitSnapshot]:
        """Return two fixed limit windows, or fail when configured to."""
        if self._fail:
            raise LimitsUnavailableError("fake limits endpoint unavailable")
        return [
            LimitSnapshot(
                provider=self.provider,
                ts=_BASE_TS,
                window_kind="hourly",
                utilization_pct=42.0,
                resets_at=_BASE_TS + timedelta(hours=1),
                raw={"utilization": 42},
            ),
            LimitSnapshot(
                provider=self.provider,
                ts=_BASE_TS,
                window_kind="weekly",
                utilization_pct=13.5,
                resets_at=_BASE_TS + timedelta(days=6),
                raw={"utilization": 13.5},
            ),
        ]


class FakePricingStrategy(PricingStrategy):
    """Prices every token category linearly at the price row's rates."""

    provider = FAKE_PROVIDER

    def cost(self, event: UsageEvent, price: PriceRow) -> Decimal:
        """Return the plain per-MTok linear cost of ``event``."""
        mtok = Decimal(1_000_000)
        total = (
            event.input_tokens * price.input_per_mtok
            + event.output_tokens * price.output_per_mtok
            + event.cache_read_tokens * price.cache_read_per_mtok
            + event.cache_write_short_tokens * price.cache_write_short_per_mtok
            + event.cache_write_long_tokens * price.cache_write_long_per_mtok
        ) / mtok
        return total.quantize(Decimal("0.000001"))


def register(registry: ProviderRegistry) -> None:
    """Register all fake adapters on ``registry``."""
    registry.register_usage_source(FAKE_PROVIDER, FakeUsageSource)
    registry.register_limits_source(FAKE_PROVIDER, FakeLimitsSource)
    registry.register_pricing(FakePricingStrategy())
