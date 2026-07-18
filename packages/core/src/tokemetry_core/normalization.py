"""Central provider alias normalization and the seed provider catalog.

Every alternate spelling of a provider (``z.ai``, ``claude``, ``codex``) maps
to its canonical lowercase id here (FR-PROVIDER-002/003), so no other module
hardcodes provider spellings. Unknown ids pass through lowercased and
unchanged -- ingest policy, not this function, decides whether to accept an
unregistered provider (FR-PROVIDER-005). The rule set is versioned so persisted
alias mappings can record which version produced them.
"""

from __future__ import annotations

from tokemetry_core.models import ProviderDescriptor

#: Bump when the alias rules below change so persisted mappings stay auditable.
PROVIDER_NORMALIZATION_VERSION = 1

ANTHROPIC_DESCRIPTOR = ProviderDescriptor(
    id="anthropic",
    display_name="Anthropic",
    aliases=("claude", "claude-code", "claude_code"),
    pricing_strategy="anthropic",
    limit_semantics="anthropic_oauth_windows",
    supported_dimensions=("machine", "model", "project", "session"),
)

OPENAI_DESCRIPTOR = ProviderDescriptor(
    id="openai",
    display_name="OpenAI",
    aliases=("codex", "codex-cli", "openai-codex"),
    pricing_strategy="openai",
    limit_semantics="openai_windows",
    supported_dimensions=("machine", "model", "project", "session"),
)

ZAI_DESCRIPTOR = ProviderDescriptor(
    id="zai",
    display_name="Z.ai",
    aliases=("z.ai", "z-ai", "z_ai"),
    pricing_strategy="zai",
    limit_semantics="zai_coding_plan",
    supported_dimensions=("machine", "model", "project", "session"),
)

#: Providers registered by default (FR-PROVIDER-008). The ``fake`` test
#: provider registers its own descriptor from ``providers/fake.py``.
SEED_PROVIDER_DESCRIPTORS: tuple[ProviderDescriptor, ...] = (
    ANTHROPIC_DESCRIPTOR,
    OPENAI_DESCRIPTOR,
    ZAI_DESCRIPTOR,
)


def _build_alias_index(descriptors: tuple[ProviderDescriptor, ...]) -> dict[str, str]:
    """Map each canonical id and lowercased alias to its canonical id."""
    index: dict[str, str] = {}
    for descriptor in descriptors:
        index[descriptor.id] = descriptor.id
        for alias in descriptor.aliases:
            index[alias] = descriptor.id
    return index


_ALIAS_TO_CANONICAL = _build_alias_index(SEED_PROVIDER_DESCRIPTORS)


def normalize_provider(raw: str) -> str:
    """Return the canonical lowercase provider id for any spelling of ``raw``.

    Pure, case-insensitive, and idempotent. A known alias resolves to its
    canonical id; an unknown provider passes through stripped and lowercased --
    that is not an error here, since ingest policy decides acceptance
    (FR-PROVIDER-005).
    """
    cleaned = raw.strip().lower()
    return _ALIAS_TO_CANONICAL.get(cleaned, cleaned)
