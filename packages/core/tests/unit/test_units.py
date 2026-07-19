"""Unit tests for the billable unit-type vocabulary."""

import pytest
from tokemetry_core.units import (
    ALL_UNIT_TYPES,
    BILLABLE_UNIT_TYPES,
    MAX_BILLABLE_UNITS,
    TOKEN_UNIT_TYPES,
    UnitTypeError,
    validate_billable_units,
)


def test_token_and_billable_are_disjoint() -> None:
    assert TOKEN_UNIT_TYPES.isdisjoint(BILLABLE_UNIT_TYPES)
    assert ALL_UNIT_TYPES == TOKEN_UNIT_TYPES | BILLABLE_UNIT_TYPES


def test_validate_accepts_non_token_units() -> None:
    units = {"web_search_request": 2.0, "image_input": 1.0}
    assert validate_billable_units(units) == units


def test_validate_rejects_token_type() -> None:
    with pytest.raises(UnitTypeError, match="token unit type"):
        validate_billable_units({"input_token": 100.0})


def test_validate_rejects_unknown_type() -> None:
    with pytest.raises(UnitTypeError, match="unknown billable unit type"):
        validate_billable_units({"quantum_flops": 1.0})


def test_validate_rejects_negative_quantity() -> None:
    with pytest.raises(UnitTypeError, match="non-negative"):
        validate_billable_units({"tool_call": -1.0})


def test_validate_rejects_too_many_entries() -> None:
    with pytest.raises(UnitTypeError, match="exceed"):
        validate_billable_units({f"unit_{i}": 1.0 for i in range(MAX_BILLABLE_UNITS + 1)})
