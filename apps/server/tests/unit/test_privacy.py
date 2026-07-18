"""Unit and fuzz tests for the v2 privacy validation layer."""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from typing import Any

from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.services.privacy import (
    PrivacyPolicy,
    PrivacyValidator,
    ValidationIssue,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

#: Content-like keys from FR-PRIV-002 the fuzzer smuggles into payloads.
_FORBIDDEN_KEYS = (
    "prompt",
    "response",
    "message",
    "content",
    "text",
    "input_text",
    "output_text",
    "arguments",
    "file_path",
    "path",
    "code",
    "snippet",
    "reasoning_content",
)

#: Keys guaranteed free of any content token, for building benign nesting.
_SAFE_KEYS = ("alpha", "beta", "gamma", "node", "item", "group", "tier", "zone")


def _event(**overrides: Any) -> UsageEventV2:
    """Build a valid v2 event, applying keyword overrides."""
    defaults: dict[str, Any] = {
        "schema_version": 2,
        "event_id": "anthropic:req_1",
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "source": SourceRef(type=SourceType.GATEWAY, name="proxy", version="1"),
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


def _codes(issues: list[ValidationIssue]) -> set[str]:
    """The set of issue codes."""
    return {issue.code for issue in issues}


def _all_keys(value: Any) -> set[str]:
    """Every mapping key anywhere in a JSON-shaped value."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys |= _all_keys(child)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item)
    return keys


class TestCleanEvents:
    """Well-formed events pass."""

    def test_minimal_event_ok(self) -> None:
        result = PrivacyValidator().sanitize(_event())
        assert result.ok
        assert result.issues == ()

    def test_allowed_extra_namespaces_and_dimensions_ok(self) -> None:
        event = _event(
            extra={"anthropic": {"beta_header": "x"}, "gateway": {"region": "us"}},
            dimensions={"team": "platform", "cost_center": "RND"},
        )
        assert PrivacyValidator().sanitize(event).ok


class TestContentKeys:
    """Prohibited content-like keys under reject and strip modes."""

    def test_rejects_nested_content_key(self) -> None:
        event = _event(extra={"anthropic": {"prompt": "secret"}})
        issues = PrivacyValidator().issues(event)
        assert "content_key" in _codes(issues)
        assert any(i.field_path == "extra.anthropic.prompt" for i in issues)

    def test_rejects_deeply_nested_content_key(self) -> None:
        event = _event(extra={"gateway": {"node": {"item": {"file_path": "x"}}}})
        issues = PrivacyValidator().issues(event)
        assert "content_key" in _codes(issues)

    def test_strip_mode_removes_content_key(self) -> None:
        policy = PrivacyPolicy(mode="strip")
        event = _event(extra={"anthropic": {"prompt": "secret", "region": "us"}})
        result = PrivacyValidator(policy).sanitize(event)
        assert result.ok
        assert result.stripped == ("extra.anthropic.prompt",)
        assert "prompt" not in _all_keys(result.event.extra)
        assert result.event.extra == {"anthropic": {"region": "us"}}

    def test_content_key_matching_is_case_and_separator_insensitive(self) -> None:
        event = _event(extra={"gateway": {"Input_Text": "x"}})
        assert "content_key" in _codes(PrivacyValidator().issues(event))


class TestExtraNamespaces:
    """Top-level extra keys must be the provider or an allowed namespace."""

    def test_rejects_foreign_namespace(self) -> None:
        event = _event(extra={"openai": {"region": "us"}})
        issues = PrivacyValidator().issues(event)
        assert "extra_namespace_not_allowed" in _codes(issues)

    def test_provider_and_gateway_namespaces_allowed(self) -> None:
        event = _event(extra={"anthropic": {}, "gateway": {}})
        assert PrivacyValidator().sanitize(event).ok


class TestDimensions:
    """Dimension allowlist and bounds (D-004, FR-EVENT-020)."""

    def test_rejects_non_allowlisted_key(self) -> None:
        event = _event(dimensions={"secret": "x"})
        assert "dimension_not_allowed" in _codes(PrivacyValidator().issues(event))

    def test_rejects_too_many_dimensions(self) -> None:
        policy = PrivacyPolicy(
            dimension_allowlist=frozenset(f"d{i}" for i in range(20)),
            max_dimensions=16,
        )
        event = _event(dimensions={f"d{i}": "v" for i in range(17)})
        assert "too_many_dimensions" in _codes(PrivacyValidator(policy).issues(event))

    def test_rejects_key_too_long(self) -> None:
        long_key = "team" + "x" * 70
        policy = PrivacyPolicy(dimension_allowlist=frozenset({long_key}))
        event = _event(dimensions={long_key: "v"})
        assert "dimension_key_too_long" in _codes(PrivacyValidator(policy).issues(event))

    def test_rejects_value_too_long(self) -> None:
        event = _event(dimensions={"team": "x" * 300})
        assert "dimension_value_too_long" in _codes(PrivacyValidator().issues(event))


class TestToolHistogram:
    """Tool-name histogram gate and bounds (D-005)."""

    def test_rejected_when_disabled(self) -> None:
        event = _event(tool_histogram={"read": 3})
        assert "tool_names_disabled" in _codes(PrivacyValidator().issues(event))

    def test_accepted_when_enabled(self) -> None:
        policy = PrivacyPolicy(tool_names_enabled=True)
        event = _event(tool_histogram={"read": 3, "write": 1})
        assert PrivacyValidator(policy).sanitize(event).ok

    def test_rejects_too_many_names(self) -> None:
        policy = PrivacyPolicy(tool_names_enabled=True, max_tool_names=32)
        event = _event(tool_histogram={f"tool_{i}": 1 for i in range(33)})
        assert "too_many_tool_names" in _codes(PrivacyValidator(policy).issues(event))

    def test_rejects_name_too_long(self) -> None:
        policy = PrivacyPolicy(tool_names_enabled=True, max_tool_name_length=64)
        event = _event(tool_histogram={"t" * 65: 1})
        assert "tool_name_too_long" in _codes(PrivacyValidator(policy).issues(event))

    def test_rejects_negative_count(self) -> None:
        policy = PrivacyPolicy(tool_names_enabled=True)
        event = _event(tool_histogram={"read": -1})
        assert "tool_count_negative" in _codes(PrivacyValidator(policy).issues(event))


class TestSizeAndDepth:
    """Whole-event size and nesting bounds (FR-EVENT-028, NFR-SEC-004)."""

    def test_rejects_oversized_event(self) -> None:
        event = _event(extra={"gateway": {"blob": "x" * 40_000}})
        assert "event_too_large" in _codes(PrivacyValidator().issues(event))

    def test_rejects_too_deep_event(self) -> None:
        nested: dict[str, Any] = {"leaf": 1}
        for _ in range(10):
            nested = {"node": nested}
        event = _event(extra={"gateway": nested})
        policy = PrivacyPolicy(max_json_depth=6)
        assert "event_too_deep" in _codes(PrivacyValidator(policy).issues(event))


class TestPerformance:
    """Validation of a full batch stays well under the ingest budget."""

    def test_hundred_events_validate_quickly(self) -> None:
        validator = PrivacyValidator()
        events = [
            _event(
                event_id=f"anthropic:req_{i}",
                extra={"anthropic": {"region": "us"}, "gateway": {"tier": "std"}},
                dimensions={"team": "platform"},
            )
            for i in range(100)
        ]
        start = time.perf_counter()
        for event in events:
            assert validator.sanitize(event).ok
        # Generous bound: 100 small events validate in milliseconds; this only
        # catches pathological blow-ups, never normal timing jitter.
        assert time.perf_counter() - start < 5.0


class TestProhibitedKeyFuzz:
    """Property-style fuzz: a forbidden key at any depth is always caught."""

    def _nested(self, rng: random.Random, depth: int) -> dict[str, Any]:
        """A benign nested dict of the given depth using only safe keys."""
        node: dict[str, Any] = {rng.choice(_SAFE_KEYS): rng.randint(0, 9)}
        for _ in range(depth):
            node = {rng.choice(_SAFE_KEYS): node}
        return node

    def _inject(
        self, rng: random.Random, node: dict[str, Any], key: str
    ) -> None:
        """Insert ``key`` into ``node`` or a random descendant dict."""
        current = node
        while True:
            children = [v for v in current.values() if isinstance(v, dict)]
            if not children or rng.random() < 0.4:
                current[key] = 1
                return
            current = rng.choice(children)

    def test_forbidden_key_at_random_depth_is_detected(self) -> None:
        rng = random.Random(20260710)
        reject = PrivacyValidator()
        strip = PrivacyValidator(PrivacyPolicy(mode="strip"))
        for _ in range(300):
            payload = self._nested(rng, rng.randint(0, 4))
            forbidden = rng.choice(_FORBIDDEN_KEYS)
            self._inject(rng, payload, forbidden)
            event = _event(extra={"gateway": payload})

            # Reject mode must surface a content_key issue.
            assert "content_key" in _codes(reject.issues(event))
            # Strip mode must remove every content-like key.
            cleaned = strip.sanitize(event)
            leftover = _all_keys(cleaned.event.extra)
            assert not any(
                token in "".join(c for c in k.lower() if c.isalnum())
                for k in leftover
                for token in ("prompt", "response", "message", "content",
                              "text", "arguments", "path", "code", "snippet")
            )
