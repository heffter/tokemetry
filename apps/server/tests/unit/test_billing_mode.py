"""Billing-mode resolution precedence and settings override-map parsing (D-007)."""

from __future__ import annotations

import pytest
from tokemetry_server.config import Settings
from tokemetry_server.services.billing_mode import (
    API_BILLED,
    SUBSCRIPTION,
    resolve_billing_mode,
)


def test_explicit_subscription_source_wins_over_override() -> None:
    # An operator-configured subscription source is authoritative.
    assert resolve_billing_mode(SUBSCRIPTION, "m1", {"m1": API_BILLED}) == SUBSCRIPTION


def test_default_source_defers_to_machine_override() -> None:
    # A source still at the default mode is refined by the account override.
    assert resolve_billing_mode(API_BILLED, "m1", {"m1": SUBSCRIPTION}) == SUBSCRIPTION


def test_unattributed_event_uses_machine_override() -> None:
    assert resolve_billing_mode(None, "m1", {"m1": SUBSCRIPTION}) == SUBSCRIPTION


def test_falls_back_to_default_when_nothing_matches() -> None:
    assert resolve_billing_mode(API_BILLED, "m1", {}) == API_BILLED
    assert resolve_billing_mode(None, None, {}) == API_BILLED


def test_override_only_applies_to_the_named_machine() -> None:
    assert resolve_billing_mode(None, "other", {"m1": SUBSCRIPTION}) == API_BILLED


def test_override_map_parses_entries() -> None:
    settings = Settings(billing_mode_overrides="maxbook=subscription, api-box=api_billed")
    assert settings.billing_mode_override_map == {
        "maxbook": SUBSCRIPTION,
        "api-box": API_BILLED,
    }


def test_override_map_is_empty_by_default() -> None:
    assert Settings().billing_mode_override_map == {}


def test_override_map_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="invalid billing_mode override"):
        _ = Settings(billing_mode_overrides="maxbook=free").billing_mode_override_map


def test_override_map_rejects_malformed_entry() -> None:
    with pytest.raises(ValueError, match="invalid billing_mode override"):
        _ = Settings(billing_mode_overrides="maxbook").billing_mode_override_map
