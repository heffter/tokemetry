"""Pinned OpenTelemetry GenAI semantic-convention mapping (Task 71.2, D-013).

The normative mapping from OpenTelemetry generative-AI span attributes to v2
attempt-event fields, pinned to a specific semantic-conventions release so the
OTLP bridge (Tasks 71.3/71.4) is deterministic. The pinned version is stamped
onto every mapped event under ``extra.otel.semconv_version`` (FR-OTEL-006).

The mapping table lives here (not only in the doc) so the converter and its
tests consume one source of truth; ``docs/architecture/otel-mapping.md`` is the
prose companion. Content attributes are listed for *stripping*, never mapping
(FR-OTEL-002/007).
"""

from __future__ import annotations

#: Pinned OpenTelemetry semantic-conventions release implemented here. Verify
#: against the opentelemetry.io semconv release notes when bumping; the GenAI
#: conventions were still marked development upstream at this pin, so the bridge
#: treats unknown attributes leniently (kept under ``extra.otel``).
SEMCONV_VERSION = "1.30.0"

# --- Direct scalar attribute -> v2 field mappings --------------------------- #
# gen_ai.system is normalized to a provider id via the provider registry's
# aliases; the others are copied through.
PROVIDER_ATTR = "gen_ai.system"
REQUESTED_MODEL_ATTR = "gen_ai.request.model"
RESPONSE_MODEL_ATTR = "gen_ai.response.model"
OPERATION_ATTR = "gen_ai.operation.name"
ERROR_TYPE_ATTR = "error.type"

#: Semconv usage attribute -> v2 token counter.
TOKEN_ATTR_TO_FIELD: dict[str, str] = {
    "gen_ai.usage.input_tokens": "input_tokens",
    "gen_ai.usage.output_tokens": "output_tokens",
    # Cache/reasoning tiers are provider extensions in this semconv version;
    # they are mapped when present and otherwise fall back to extra.otel.
    "gen_ai.usage.cache_read_tokens": "cache_read_tokens",
    "gen_ai.usage.reasoning_tokens": "reasoning_tokens",
}

#: Content-bearing attributes stripped unconditionally before any event is
#: built -- never stored (FR-OTEL-007). Matching is by exact key or the two
#: content prefixes for event bodies.
CONTENT_ATTRS: frozenset[str] = frozenset(
    {
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
    }
)
CONTENT_ATTR_PREFIXES: tuple[str, ...] = ("gen_ai.prompt.", "gen_ai.completion.")

#: Namespace under ``extra`` for retained-but-unmapped span attributes and the
#: recorded semconv version.
EXTRA_NAMESPACE = "otel"

#: v2 defaults for concepts a span does not carry (finality/sequence are set for
#: a single final attempt; billing mode defaults to api_billed).
SPAN_DERIVED_DEFAULTS: dict[str, object] = {
    "event_kind": "attempt",
    "finality": "final",
    "sequence": 1,
}


def is_content_attr(key: str) -> bool:
    """Whether ``key`` is a content-bearing attribute to strip."""
    return key in CONTENT_ATTRS or key.startswith(CONTENT_ATTR_PREFIXES)
