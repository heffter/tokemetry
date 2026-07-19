"""Billable unit-type vocabulary (PRD Section 12.7.2, D-006).

Every priced quantity is one ``unit_type``. **Token** unit types map one-to-one
onto the typed counters of ``usage_events_v2`` and are priced from those columns;
they are never stored in the ``billable_units`` table (PP-007, FR-DIM-007).
**Non-token** unit types -- hosted-tool charges, media seconds, storage -- have
no dedicated column, so they are carried in an event's ``billable_units`` map and
stored per event (FR-DIM-008). Unknown counters stay in provider extension
metadata until promoted here (FR-EVENT-016).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Token units, priced from the ``usage_events_v2`` counters (batch variants are
#: the same counts priced at the batch mode rate). Never stored per event.
TOKEN_UNIT_TYPES: frozenset[str] = frozenset(
    {
        "input_token",
        "output_token",
        "cache_read_token",
        "cache_write_short_token",
        "cache_write_long_token",
        "reasoning_token",
        "batch_input_token",
        "batch_output_token",
    }
)

#: Non-token units carried in an event's ``billable_units`` map and stored in the
#: ``billable_units`` table.
BILLABLE_UNIT_TYPES: frozenset[str] = frozenset(
    {
        "request",
        "web_search_request",
        "tool_call",
        "image_input",
        "image_output",
        "audio_input_second",
        "audio_output_second",
        "video_second",
        "storage_byte_hour",
    }
)

#: Every recognized unit type.
ALL_UNIT_TYPES: frozenset[str] = TOKEN_UNIT_TYPES | BILLABLE_UNIT_TYPES

#: Maximum distinct entries in one event's ``billable_units`` map.
MAX_BILLABLE_UNITS = 32


class UnitTypeError(ValueError):
    """A billable-units map used an unknown, token, or invalid unit type."""


def validate_billable_units(units: Mapping[str, float]) -> dict[str, float]:
    """Validate a billable-units map; return it as a plain ``dict``.

    Keys must be non-token billable unit types (:data:`BILLABLE_UNIT_TYPES`);
    token unit types are rejected because they belong on the event counters
    (PP-007), as are unknown types. Quantities must be non-negative, and the map
    is bounded to :data:`MAX_BILLABLE_UNITS` entries.

    Raises:
        UnitTypeError: On an unknown or token unit type, a negative quantity, or
            too many entries.
    """
    if len(units) > MAX_BILLABLE_UNITS:
        raise UnitTypeError(
            f"{len(units)} billable units exceed the {MAX_BILLABLE_UNITS} limit"
        )
    result: dict[str, float] = {}
    for unit_type, quantity in units.items():
        if unit_type in TOKEN_UNIT_TYPES:
            raise UnitTypeError(
                f"token unit type {unit_type!r} belongs on the event counters, "
                "not the billable_units map"
            )
        if unit_type not in BILLABLE_UNIT_TYPES:
            raise UnitTypeError(f"unknown billable unit type: {unit_type!r}")
        if quantity < 0:
            raise UnitTypeError(f"quantity for {unit_type!r} must be non-negative")
        result[unit_type] = quantity
    return result


def known_unit_types() -> Iterable[str]:
    """Every recognized unit type, sorted (token and non-token)."""
    return sorted(ALL_UNIT_TYPES)
