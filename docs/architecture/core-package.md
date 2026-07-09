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

## Provider registry (`registry.py`)

`ProviderRegistry` maps provider names to usage-source factories,
limits-source factories, and pricing strategy instances. Registration is
explicit: each provider module exposes `register(registry)`, called by the
application at startup. Unknown lookups raise `UnknownProviderError`.

## Fake provider (`providers/fake.py`)

A deterministic reference implementation of all three interfaces, shipped in
the package so core, collector, and server test suites exercise the same
pipeline without any real provider. It also acts as the guard that keeps
provider-specific assumptions out of core code paths.
