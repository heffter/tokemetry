"""Sanity validation for ingested data.

Rejects physically impossible or abusive payloads before they reach the
database (inspired by viberank's anti-garbage submission checks). Pydantic
already enforces non-negativity and field bounds; these checks add
cross-field and range sanity that schema types cannot express.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tokemetry_core.models import LimitSnapshot, UsageEvent

#: Upper bound on any single token counter -- far above any real request,
#: low enough to catch corruption/overflow. 10 billion tokens.
_MAX_TOKENS = 10_000_000_000

#: How far into the future a timestamp may be before it is rejected, to
#: absorb minor clock skew between machines.
_MAX_CLOCK_SKEW = timedelta(hours=2)

#: Utilization is a percentage; allow a little overage above 100.
_MAX_UTILIZATION = 1000.0


class ValidationError(ValueError):
    """A payload failed a sanity check; the whole batch is rejected."""


def _now() -> datetime:
    """Current UTC time (indirected for test control)."""
    return datetime.now(UTC)


def validate_event(event: UsageEvent) -> None:
    """Validate one usage event; raise :class:`ValidationError` on failure."""
    for name, value in (
        ("input_tokens", event.input_tokens),
        ("output_tokens", event.output_tokens),
        ("cache_read_tokens", event.cache_read_tokens),
        ("cache_write_short_tokens", event.cache_write_short_tokens),
        ("cache_write_long_tokens", event.cache_write_long_tokens),
    ):
        if value > _MAX_TOKENS:
            raise ValidationError(f"{name}={value} exceeds sane maximum for {event.event_id}")
    if event.ts > _now() + _MAX_CLOCK_SKEW:
        raise ValidationError(f"timestamp {event.ts.isoformat()} is too far in the future")


def validate_limit(snapshot: LimitSnapshot) -> None:
    """Validate one limit snapshot; raise :class:`ValidationError`."""
    if snapshot.utilization_pct > _MAX_UTILIZATION:
        raise ValidationError(
            f"utilization {snapshot.utilization_pct} exceeds sane maximum"
        )
    if snapshot.ts > _now() + _MAX_CLOCK_SKEW:
        raise ValidationError(f"timestamp {snapshot.ts.isoformat()} is too far in the future")
