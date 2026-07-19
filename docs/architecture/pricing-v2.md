# Provider-neutral pricing and cost (v2, TOK-5)

The v2 pricing stack prices any provider's billing model from one generic
rate-card table, keeps cost strictly separate from usage, computes cost out of
the ingest path, and treats every price change as a reviewable, auditable,
reversible operation. This document is the canonical reference for the model;
`pricing.md` covers the v1 per-MTok engine that still runs alongside it during
the migration.

## Rate cards (the pricing grain)

A price is stored per single billable `unit_type` for a model, effective over a
date range -- the `rate_cards` table (D-006). One row is one
`(provider, native_model, unit_type, effective_from, service_tier, mode,
context_bracket, priority)` grain; `unit_price` is `Numeric(20,10)` USD **per
single unit** (per token for token units), so no provider's billing model needs
a bespoke column. `effective_to` is null for an open (current) row and set when
the price is superseded; `source` and `priority`/`override` carry provenance and
precedence.

## Unit vocabulary

`tokemetry_core.units` enumerates every priced `unit_type` (D-006):

- **Token units** map one-to-one onto the `usage_events_v2` counters and are
  priced from those columns, never stored per event: `input_token`,
  `output_token`, `cache_read_token`, `cache_write_short_token`,
  `cache_write_long_token`, `reasoning_token`, and the `batch_*` variants.
- **Non-token units** (`web_search_request`, `image_input`,
  `audio_output_second`, ...) have no counter, so they live in an event's
  `billable_units` map and the `billable_units` table.

## Resolution and precedence

`services/pricing_v2.resolve_rate(provider, native_model, unit_type, at, tier,
mode, context_bracket)` returns the single best rate card effective at a
timestamp. Precedence (FR-PRICE-004, "higher wins"): tier-specific >
bracket-specific > `priority` > `override` > latest `effective_from`. Model ids
resolve exactly first, then by date-stripped base name. Two open cards on the
same grain+priority are a conflict: `check_overlap` rejects an overlapping
insert (`OverlapError`, FR-PRICE-005), and a curated `official` row outranks a
`litellm` row by carrying a higher `priority` (FR-PRICE-021).

## Provider pricing strategies

A `ProviderPricingStrategyV2` (`tokemetry_core.interfaces`) declares the token
unit types a provider bills and its reasoning rule; the cost engine is otherwise
provider-neutral, so a new provider is a plugin with no engine change (PP-011,
NFR-MAIN-002). Built-ins: anthropic (five units incl. both cache-write TTL
tiers; reasoning always folds into output), openai/zai (cached input as
`cache_read_token`, no TTL write tiers per FR-DIM-006; reasoning priced
separately only when a `reasoning_token` rate exists, FR-PRICE-011), and a
token-linear generic fallback.

## Cost engine and status

`CostEngineV2` prices a `final` `attempt` (snapshots are never priced,
FR-COST-001): it gathers consumed unit quantities via the provider strategy,
resolves each through the rate cards, sums `Decimal` amounts quantized to a
micro-unit, and writes a `computed_costs` row stamped with the pricing-state
version. Status (FR-COST-006): all units priced -> `priced`; some without a
rate -> `partial` with `missing_units`; none -> `unpriced` (null amount); a
resolver error -> `error`. Cost never rejects or blocks ingest (FR-COST-008).

Cost runs **out of the ingest path** (FR-COST-009, NFR-PERF-005): the async
worker (`services/cost_worker.sweep_uncosted_costs`) prices final attempts that
lack an active `computed_costs` row, giving eventual coverage after a burst and
catching up after a restart. Exactly one `computed_costs` row per event is
`active` (FR-COST-001).

## Billing modes and dual metrics

Every cost is `api_billed` (actual spend) or `subscription` (equivalent value,
no real spend), never merged (D-007, FR-COST-011/012). The mode is resolved from
the event's `sources.billing_mode`, with an account-level machine override for
usage whose source keeps the default (a v1 collector on a Max machine).
`api_billed` rows populate `amount`; `subscription` rows populate
`subscription_equivalent_amount` with `amount` null.
`services/cost_queries.dual_cost_metrics` reports `actual_spend_usd` and
`subscription_value_usd` as separate sums with no combined total.

## Import and approval flow

Prices reach `rate_cards` through a reviewable import, never a silent rewrite
(D-015, FR-PRICE-015/016). Sources (`tokemetry_core.pricing.sources`) transform
the LiteLLM database (anthropic/openai) and a curated official set (Z.ai, plus
overrides) into rows. `compute_import_diff` classifies each row against the
stored open card as new/superseded/unchanged/conflict and returns a sha256
`digest` without persisting; `apply_import` recomputes the diff, requires the
caller's digest to match (a change since the dry run is rejected), then closes
superseded rows (`effective_to = effective_from - 1 day`) and inserts new rows,
writing an audit entry. A stored card already effective on the import date is a
conflict, left untouched -- **past effective periods are never rewritten**
(FR-PRICE-016). `POST /api/v2/pricing/import?dry_run=` drives it (admin:pricing).

## Repricing, reversibility, and pricing_version

`pricing_version` is an `app_settings` counter; `resolve_rate` and the cost
status are stamped with it. A price edit does not retroactively change stored
costs -- an explicit **reprice** does: `services/repricing.reprice` bumps the
version, recomputes a range as new `computed_costs` rows under the new version,
flips the active row per event, and retains the prior rows so the operation is
reversible (FR-PRICE-019/020). **revert** re-activates a named prior version for
the range, restoring the exact prior amounts. Both are audited.

## Reports

`GET /api/v2/pricing/reports/unpriced` aggregates active unpriced/partial events
by model (from `computed_costs`); `.../reports/unknown-models` lists
unknown-model observations (from `data_quality_events`), so an operator sees
exactly which models still need a rate card (US-010).

## Historical stability guarantee

Because cost is resolved at each event's own timestamp and past periods are
never rewritten, adding a price effective today never changes an already-computed
historical cost (AC-007, FR-PRICE-001). The regression suite
(`tests/integration/test_pricing_historical_stability.py`) proves this by
snapshotting costs, adding a today-effective rate, repricing, and asserting the
historical amounts are bit-identical; it also proves the v2 rate-card engine
reproduces the v1 per-MTok cost to the last decimal (FR-PRICE-009) and that
unpriced events and unknown models are visible end to end.

## Requirements map (TOK-5)

| Requirement | Where |
|---|---|
| FR-PRICE-001 historical costs immutable | resolution at event ts; reprice-only recompute; regression suite |
| FR-PRICE-002 partial cost with missing units | `CostEngineV2._compute`, `partial` status |
| FR-PRICE-004 resolution precedence | `pricing_v2._precedence` |
| FR-PRICE-005 overlap rejection | `pricing_v2.check_overlap` |
| FR-PRICE-009 v1<->v2 migration equality | rate-card transform; regression suite |
| FR-PRICE-010 distinct cache-write TTL tiers | anthropic strategy units |
| FR-PRICE-011 reasoning as output unless rated | `ReasoningBilling.SEPARATE_IF_RATED` |
| FR-PRICE-012 Z.ai cached input | zai strategy + curated source |
| FR-PRICE-013 additive hosted-tool fees | billable units in `quantities` |
| FR-PRICE-015/016 audited import, no silent past rewrite | `pricing_import` diff/apply |
| FR-PRICE-019/020 reprice + revert | `services/repricing` |
| FR-PRICE-021 official > litellm precedence | curated `priority`; `resolve_rate` |
| FR-PRICE-022 pricing reports | `pricing_admin` reports |
| FR-COST-001 one active cost, snapshots unpriced | `computed_costs.record_cost` |
| FR-COST-002 reversible repricing | `repricing.revert` |
| FR-COST-006 cost status set | `CostEngineV2` statuses |
| FR-COST-008 cost never rejects ingest | `error` status, out-of-path worker |
| FR-COST-009 async costing | `cost_worker` |
| FR-COST-011/012 billing modes, never merged | `billing_mode`, `cost_queries` |
