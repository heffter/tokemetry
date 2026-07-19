"""Unit tests for provider alias normalization and the registry descriptors."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tokemetry_core.models import ProviderDescriptor
from tokemetry_core.normalization import (
    ANTHROPIC_DESCRIPTOR,
    OPENAI_DESCRIPTOR,
    PROVIDER_NORMALIZATION_VERSION,
    SEED_PROVIDER_DESCRIPTORS,
    ZAI_DESCRIPTOR,
    normalize_provider,
)
from tokemetry_core.providers import anthropic as anthropic_provider
from tokemetry_core.providers.claude_code import ClaudeCodeJsonlSource
from tokemetry_core.registry import ProviderRegistry, UnknownProviderError


class TestNormalizeProvider:
    """Alias normalization is central, case-insensitive, and idempotent."""

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            ("anthropic", "anthropic"),
            ("claude", "anthropic"),
            ("claude-code", "anthropic"),
            ("claude_code", "anthropic"),
            ("openai", "openai"),
            ("codex", "openai"),
            ("codex-cli", "openai"),
            ("openai-codex", "openai"),
            ("zai", "zai"),
            ("z.ai", "zai"),
            ("z-ai", "zai"),
            ("z_ai", "zai"),
        ],
    )
    def test_known_aliases_resolve(self, raw: str, canonical: str) -> None:
        assert normalize_provider(raw) == canonical

    @pytest.mark.parametrize("raw", ["ANTHROPIC", "Claude", "Z.AI", "CoDeX", "  zai  "])
    def test_case_and_whitespace_insensitive(self, raw: str) -> None:
        # Every spelling here belongs to a known provider regardless of casing.
        assert normalize_provider(raw) in {"anthropic", "zai", "openai"}

    def test_unknown_passes_through_lowercased(self) -> None:
        assert normalize_provider("Mistral") == "mistral"
        assert normalize_provider("  Some-New-Provider ") == "some-new-provider"

    @pytest.mark.parametrize(
        "raw", ["claude", "Z.AI", "codex", "anthropic", "unknown-vendor"]
    )
    def test_idempotent(self, raw: str) -> None:
        once = normalize_provider(raw)
        assert normalize_provider(once) == once


class TestProviderDescriptor:
    """Descriptor invariants: canonical id, lowercased aliases, frozen."""

    def test_seed_ids(self) -> None:
        assert {d.id for d in SEED_PROVIDER_DESCRIPTORS} == {"anthropic", "openai", "zai"}

    def test_aliases_are_lowercased(self) -> None:
        descriptor = ProviderDescriptor(id="x", display_name="X", aliases=("Foo", "BAR"))
        assert descriptor.aliases == ("foo", "bar")

    def test_non_lowercase_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProviderDescriptor(id="Anthropic", display_name="Anthropic")

    def test_padded_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProviderDescriptor(id=" anthropic ", display_name="Anthropic")

    def test_frozen(self) -> None:
        with pytest.raises(ValidationError):
            ANTHROPIC_DESCRIPTOR.display_name = "changed"

    def test_metadata_retained(self) -> None:
        # FR-PROVIDER-004: display name, aliases, pricing strategy, limit
        # semantics, and supported dimensions are all present.
        assert ANTHROPIC_DESCRIPTOR.display_name == "Anthropic"
        assert "claude" in ANTHROPIC_DESCRIPTOR.aliases
        assert ANTHROPIC_DESCRIPTOR.pricing_strategy == "anthropic"
        assert ANTHROPIC_DESCRIPTOR.limit_semantics == "anthropic_oauth_windows"
        assert ANTHROPIC_DESCRIPTOR.supported_dimensions

    def test_every_alias_normalizes_to_its_owner(self) -> None:
        for descriptor in SEED_PROVIDER_DESCRIPTORS:
            for alias in descriptor.aliases:
                assert normalize_provider(alias) == descriptor.id

    def test_anthropic_seeds_its_windows_with_the_dashboard_labels(self) -> None:
        # FR-LIMIT-012: the registry declares Anthropic's windows with exactly
        # the labels the dashboard hardcoded, so migrating to it is a
        # zero-visual-change swap.
        labels = {w.kind: w.label for w in ANTHROPIC_DESCRIPTOR.windows}
        assert labels == {
            "five_hour": "5-hour block",
            "seven_day": "Weekly",
            "seven_day_opus": "Weekly (Opus)",
            "seven_day_sonnet": "Weekly (Sonnet)",
        }
        five_hour = next(
            w for w in ANTHROPIC_DESCRIPTOR.windows if w.kind == "five_hour"
        )
        assert five_hour.period_kind == "rolling"
        assert five_hour.period_seconds == 5 * 3600
        # Windows carry an explicit display order.
        orders = [w.sort_order for w in ANTHROPIC_DESCRIPTOR.windows]
        assert orders == sorted(orders)

    def test_openai_seeds_its_primary_and_secondary_windows(self) -> None:
        # Codex reports a primary and secondary rolling window (Task 69.3).
        kinds = {w.kind for w in OPENAI_DESCRIPTOR.windows}
        assert kinds == {"primary", "secondary"}
        assert all(w.period_kind == "rolling" for w in OPENAI_DESCRIPTOR.windows)

    def test_zai_seeds_its_coding_plan_window(self) -> None:
        # Z.ai's GLM coding plan reports a prompt-count 5h window (Task 69.4).
        kinds = {w.kind for w in ZAI_DESCRIPTOR.windows}
        assert kinds == {"prompt_5h"}
        (window,) = ZAI_DESCRIPTOR.windows
        assert window.period_kind == "rolling"
        assert window.period_seconds == 5 * 3600


class TestRegistryDescriptors:
    """Descriptor registration, lookup, and normalize-then-resolve."""

    def test_register_and_lookup(self) -> None:
        registry = ProviderRegistry()
        registry.register_provider(OPENAI_DESCRIPTOR)
        assert registry.provider("openai") is OPENAI_DESCRIPTOR
        assert registry.is_provider_registered("openai")
        assert registry.providers() == ["openai"]

    def test_unknown_lookup_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(UnknownProviderError):
            registry.provider("openai")
        assert not registry.is_provider_registered("openai")

    def test_resolve_normalizes_before_lookup(self) -> None:
        registry = ProviderRegistry()
        registry.register_provider(ZAI_DESCRIPTOR)
        assert registry.resolve_provider("z.ai") is ZAI_DESCRIPTOR
        assert registry.resolve_provider("Z-AI") is ZAI_DESCRIPTOR

    def test_resolve_unregistered_returns_none(self) -> None:
        # FR-PROVIDER-005: an unregistered provider is not an error.
        registry = ProviderRegistry()
        registry.register_provider(ANTHROPIC_DESCRIPTOR)
        assert registry.resolve_provider("mistral") is None


class TestAnthropicRegistrationUnchanged:
    """FR-PROVIDER-009: existing Claude Code registration still works."""

    def test_version_constant_is_stable(self) -> None:
        assert PROVIDER_NORMALIZATION_VERSION == 1

    def test_claude_code_resolves_under_anthropic(self) -> None:
        registry = ProviderRegistry()
        anthropic_provider.register(registry)
        # The usage source is unchanged and still keyed by "anthropic".
        assert ClaudeCodeJsonlSource.provider == "anthropic"
        assert isinstance(registry.usage_source("anthropic"), ClaudeCodeJsonlSource)
        # The descriptor now rides alongside it without changing the id.
        assert registry.provider("anthropic").id == "anthropic"
        assert registry.resolve_provider("claude") is ANTHROPIC_DESCRIPTOR
