# Core package (`tokemetry_core`)

The core package contains everything that must be identical on both sides of
the wire: normalized models, the provider abstraction interfaces, and the
provider registry. Collector and server depend on it; it depends only on
pydantic.

## Normalized models (`models.py`)

| Model | Purpose |
|---|---|
| `UsageEvent` | One provider request, normalized: generic token fields (`input`, `output`, `cache_read`, `cache_write_short`, `cache_write_long`), session/project/machine context, `extra` dict for provider-specific counters. Unique per `(provider, event_id)`. |
| `DailyAggregate` | Coarse per-day, per-model totals for one-time history bootstrap imports. |
| `LimitSnapshot` | Utilization of one provider limit window (`window_kind` is provider-defined, for example `five_hour`), plus the raw payload. |
| `SourceFile` / `ParseResult` | Incremental parsing contract: discovered file with size; events plus the byte offset the next parse resumes from. |
| `PriceRow` | Date-versioned per-MTok prices; cost is computed with the row effective at the event timestamp. |
| `Provenance` | Labels every number as `official`, `local_estimate`, or `stats_cache`. |
| `ProviderDescriptor` | Canonical registry metadata for one provider: lowercase stable `id`, `display_name`, `aliases`, `pricing_strategy`, `limit_semantics`, `supported_dimensions` (FR-PROVIDER-004). `id` must be lowercase/stripped; aliases are stored lowercased. |

Conventions: all timestamps are timezone-aware (validated), token counts are
non-negative, models are frozen (immutable) and reject unknown fields, money
is `Decimal`.

## Provider interfaces (`interfaces.py`)

- `UsageSource` — `discover()`, `parse(file, offset)`, `bootstrap()`.
  Implementations are stateless; the collector owns offset persistence.
  `parse` returns events and the new offset together so the collector can
  persist both atomically.
- `LimitsSource` — `poll()` returns one `LimitSnapshot` per limit window.
  Raises `LimitsUnavailableError` when the endpoint is unreachable so
  callers can degrade to local estimates without masking bugs.
- `PricingStrategy` — `cost(event, price)` encodes provider billing rules.

Design deviation from the original spec: `parse` returns a `ParseResult`
instead of an iterator, and `poll` returns a list instead of a single
snapshot. Both changes exist because the consumers need the complete result
atomically (offset advancement, multi-window polls).

## Provider normalization (`normalization.py`)

Alias normalization is centralized here so no other module hardcodes provider
spellings (FR-PROVIDER-002/003). `normalize_provider(raw)` is pure,
case-insensitive, and idempotent: a known alias resolves to its canonical
lowercase id, and an unknown provider passes through stripped and lowercased
(never an error here — ingest policy decides acceptance, FR-PROVIDER-005).

The alias table is derived from the seed `ProviderDescriptor`s so aliases are
data, not code:

| Canonical id | Display | Aliases |
|---|---|---|
| `anthropic` | Anthropic | `claude`, `claude-code`, `claude_code` |
| `openai` | OpenAI | `codex`, `codex-cli`, `openai-codex` |
| `zai` | Z.ai | `z.ai`, `z-ai`, `z_ai` |

`PROVIDER_NORMALIZATION_VERSION` versions the rule set so persisted alias
mappings record which version produced them. The existing Claude Code provider
id `anthropic` is unchanged (FR-PROVIDER-009). The `fake` test provider carries
its own descriptor (`providers/fake.py`).

## Provider registry (`registry.py`)

`ProviderRegistry` maps provider names to usage-source factories,
limits-source factories, pricing strategy instances, and `ProviderDescriptor`s.
Registration is explicit: each provider module exposes `register(registry)`,
called by the application at startup. `register_provider(descriptor)` adds a
descriptor; `provider(id)` / `is_provider_registered(id)` / `providers()` query
them, and `resolve_provider(raw)` normalizes then looks up, returning `None` for
an unregistered provider rather than raising. Unknown source/pricing lookups
raise `UnknownProviderError`.

## Fake provider (`providers/fake.py`)

A deterministic reference implementation of all three interfaces, shipped in
the package so core, collector, and server test suites exercise the same
pipeline without any real provider. It also acts as the guard that keeps
provider-specific assumptions out of core code paths.

## Claude Code source (`providers/claude_code.py`)

`ClaudeCodeJsonlSource` is the first real `UsageSource`. It reads Claude
Code transcripts under `<claude_home>/projects/**/*.jsonl` (subagent
transcripts included) and honors `CLAUDE_CONFIG_DIR`.

Correctness rules baked in:

- **Keep-max dedup by `requestId`.** One request can emit several JSONL
  lines (streaming snapshots then the settled record) sharing a
  `requestId`; within a parse pass the entry with the largest output token
  count wins. The server's keep-max upsert resolves duplicates split across
  passes. Keeping the first entry undercounts output up to 5x (ccusage
  issue 888).
- **Cache TTL split.** `cache_creation.ephemeral_5m_input_tokens` and
  `ephemeral_1h_input_tokens` map to `cache_write_short`/`cache_write_long`.
  Legacy records with only `cache_creation_input_tokens` map to short.
- **Incomplete trailing line.** A tail without a newline is an in-progress
  write; it is not consumed and `new_offset` stays before it so the next
  pass re-reads it complete.
- **Malformed lines are counted, not dropped silently** (`ParseResult.
  malformed_lines`) so schema drift surfaces in the Machines view.
- Non-assistant lines, synthetic models, and records without usage are
  skipped.

`bootstrap()` imports `stats-cache.json` `dailyModelTokens` as
`DailyAggregate` rows (total tokens only, no input/output split), tagged
`stats_cache`. Missing or corrupt cache yields an empty list.

Verified against real local data (525 transcripts, 12,571 deduplicated
events, 0 malformed lines, dated and undated model ids).
