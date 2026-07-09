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
