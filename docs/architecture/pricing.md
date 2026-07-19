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
