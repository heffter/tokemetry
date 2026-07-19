# Pricing (`tokemetry_core.pricing`)

Cost is always computed from token counts and a date-versioned price table;
provider transcripts carry no cost figures.

## Data flow

1. **Import** (`litellm.py`): the server periodically fetches LiteLLM's
   `model_prices_and_context_window.json` and passes the parsed dict to
   `price_rows_from_litellm(data, effective_date)`. Per-token USD costs
   become per-MTok `PriceRow` values. Platform-prefixed aliases (Bedrock
   style ids containing `.` or `/`) and non-matching providers are skipped.
   Missing cache rates fall back to Anthropic's documented multipliers on
   the base input price: 1.25x for 5-minute cache writes, 2x for 1-hour
   writes, 0.1x for reads.
2. **Overrides** (`table.py`): `apply_overrides(rows, overrides)` replaces
   price fields per model from user TOML config; unknown field names are
   rejected.
3. **Resolution** (`table.py`): `PricingTable.resolve(provider, model, on)`
   picks the row with the latest `effective_date` not after `on`. Model ids
   resolve exactly first, then by date-stripped base name in both
   directions (dated query to undated row and vice versa), so
   `claude-opus-4-5-20251101` and `claude-opus-4-5` find each other.
   Failure raises `UnknownModelError`; the caller stores the event with a
   null cost and raises an alert.
4. **Strategy** (`anthropic.py`): `AnthropicPricingStrategy.cost(event,
   price)` is the dot product of token counts and row rates, quantized to
   micro-USD. The Anthropic formula (uncached input, 5m/1h cache writes,
   cache reads, output) holds because `UsageEvent.input_tokens` is the
   uncached input count and the multipliers are baked into the row.

## Defaults

`DEFAULT_ANTHROPIC_PRICE_ROWS` ships a verified snapshot (Opus 4.5, Opus
4.1, Sonnet 4.5, Haiku 4.5 as of 2026-07) so common models price correctly
before the first LiteLLM sync. Newer models without any row are ingested
with null cost and trigger the unknown-model alert; costs are backfilled by
the recompute command after a sync supplies rates.

## Server-side cost engine

The database `pricing` table is the durable, Grafana-visible, overridable
source of prices; at startup the server seeds it with the defaults, loads it
into an in-memory `PricingTable`, and builds a `CostEngine`
(`services/cost.py`). The engine's `cost(event)` is wired in as the ingest
cost function, so every event is priced as it is stored.

- Unknown model or provider (no price row / no strategy): `cost` returns
  `None`, the event stores a null cost, and the `(provider, model)` pair is
  recorded on the engine and logged for the alerting layer to surface.
- `services/pricing_repo.py` provides `seed_default_pricing`,
  `load_pricing_table`, `upsert_price_rows`, and `recompute_costs`
  (reprice all events, or only those with a null cost after new prices
  arrive).
- `services/litellm_sync.py` fetches LiteLLM's price database
  (`fetch_litellm_prices`, mockable) and upserts Anthropic rows with
  `source='litellm'`; the in-memory table is rebuilt on the next startup or
  an explicit reload.

## Provider-neutral v2 cost path

The v2 pricing engine (`services/cost_v2.py`, `CostEngineV2`) prices a final
attempt from the generic `rate_cards` grain and records a `computed_costs`
row per `(provider, event_id, pricing_version)`, kept off the usage row. Cost
never runs in the ingest path.

### Provider pricing strategy plugins

The engine splits into a provider-neutral core (rate resolution, precedence,
summation, cost status) and a small provider-specific plugin. A
`ProviderPricingStrategyV2` (`tokemetry_core.interfaces`) declares the token
`unit_type`s a provider bills and its reasoning rule; its `quantities(...)`
maps an event's counters to the priceable `unit_type` -> quantity map, which
the engine then resolves through the rate cards. Adding a provider is a plugin
with no engine change (PP-011, NFR-MAIN-002).

- `pricing/strategies/anthropic.py` -- five token units including both
  cache-write TTL tiers (5m short, 1h long, FR-PRICE-010); reasoning always
  folds into output (`FOLD_INTO_OUTPUT`) since Anthropic never bills it.
- `pricing/strategies/openai.py` -- cached input as `cache_read_token`, no
  cache-write TTL tiers (a single cache-write is not misrepresented as
  Anthropic categories, FR-DIM-006); reasoning priced as output unless a
  `reasoning_token` rate exists (`SEPARATE_IF_RATED`, FR-PRICE-011); hosted-tool
  fees (`web_search_request`, `tool_call`) additive via billable units.
- `pricing/strategies/zai.py` -- GLM cached input as `cache_read_token`
  (FR-PRICE-012); reasoning `SEPARATE_IF_RATED`.
- `pricing/strategies/generic.py` -- token-linear fallback for any unregistered
  provider, so pricing never fails for lack of a plugin.

Strategies register on the `ProviderRegistry` via `register_pricing_v2` /
`register_pricing_strategies_v2`; `registry.pricing_v2(provider)` resolves the
plugin or the generic default. The server `build_registry()` wires all four,
and `CostEngineV2` uses the built-in set when no registry is injected.

### Billing modes and dual cost metrics

Every event's cost is one of two kinds, never merged (D-007, FR-COST-012):
`api_billed` (actual out-of-pocket API spend) or `subscription`
(subscription-equivalent value at equivalent API rates, no real spend). The
mode is carried on the reporting `sources.billing_mode`, with an account-level
override map (`billing_mode_overrides`, a `machine=mode` settings list) for
usage whose source keeps the default mode -- notably v1 collector events from a
subscription (Max) machine, whose derived collector source defaults to
`api_billed`.

- Resolution (`services/billing_mode.py`, `resolve_billing_mode`): an explicit
  non-default source mode wins; otherwise the account override keyed by the
  event's machine; otherwise the source mode or the `api_billed` default.
- The cost engine writes `billing_mode` onto every `computed_costs` row.
  `api_billed` rows populate `amount`; `subscription` rows populate
  `subscription_equivalent_amount` with `amount` null. The worker and repricing
  pass the override map through from settings.
- `services/cost_queries.py` exposes `dual_cost_metrics` -> `DualCostMetrics`
  with `actual_spend_usd` (sum of `amount` over active `api_billed` rows) and
  `subscription_value_usd` (sum of `subscription_equivalent_amount` over active
  `subscription` rows). The dataclass has no combined total, so the two figures
  cannot be summed by accident. (Rollup columns land in Task 66.)

- **Async cost worker** (`services/cost_worker.py`): `sweep_uncosted_costs`
  finds final attempt events in `usage_events_v2` that lack an *active*
  `computed_costs` row and prices up to `batch_size` of them per call. The
  application runs it on a background loop (`app._cost_loop`, gated by
  `cost_worker_enabled` / `cost_worker_interval_seconds` /
  `cost_worker_batch_size`), mirroring the alert engine. Because the sweep is
  keyed on missing coverage, it gives eventual cost after an ingest burst and
  catches up the backlog after a worker restart without re-pricing covered
  events. A resolver failure records an `error`-status row and never rejects
  usage (FR-COST-008); tests disable the loop so it never runs mid-test.
- **Auditable repricing** (`services/repricing.py`): `reprice(actor, start,
  end, provider?, native_model?)` bumps the pricing-state version, recomputes
  the matching final attempts under the new version as fresh `computed_costs`
  rows, flips the active row per event, and retains the prior rows so the
  operation is reversible. `revert(actor, pricing_version, ...)` re-activates
  a named prior version for the same range, restoring the exact prior amounts.
  Both write an `audit_log` entry (actor, filters, affected count, versions).
  Rollup recomputation for the affected days is a Task 66 dependency.
- **Admin API** (`api/v2/pricing.py`): `POST /api/v2/pricing/reprice` and
  `POST /api/v2/pricing/revert`, both requiring the `admin:pricing` scope.
