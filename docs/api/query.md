# Query API

All endpoints require a bearer token (see [ingest.md](ingest.md)). The full
OpenAPI schema is at `/docs` and `/openapi.json`.

## Summary and limits

- `GET /api/v1/summary/now` -- dashboard front page: current limit gauges,
  token burn rate (tokens/min, last 60 min), predicted exhaustion for the
  5-hour window, and today's totals by model.
- `GET /api/v1/limits/current` -- latest snapshot per limit window.
- `GET /api/v1/limits/history?window_kind=five_hour&hours=24` -- utilization
  time series for one window.
- `GET /api/v1/blocks?hours=120` -- reconstructed 5-hour blocks aligned to
  official reset times, each with token/cost totals, peak per-minute burn,
  and end utilization.

Predictions extrapolate the recent slope of official utilization rather than
guessing an absolute token budget, so they work for subscription plans whose
limits are not published.

## Usage aggregation

- `GET /api/v1/usage?group_by=<dim>&from=<date>&to=<date>` with optional
  `provider`, `machine`, `model`, `project` filters.
- `group_by`: `day`, `provider`, `model`, `machine`, `project` (served from
  `daily_rollups`), or `session` (served from `usage_events`).
- `GET /api/v1/sessions?limit=100` -- recent sessions, newest first.
- `GET /api/v1/machines` -- fleet view with per-machine totals and last-seen.
- `GET /api/v1/heatmap?from&to` -- daily calendar plus a weekday x hour punch
  card.
- `GET /api/v1/cost?from&to` -- total known cost and, when
  `TOKEMETRY_SUBSCRIPTION_MONTHLY_USD` is set, the value multiple vs the
  prorated subscription price.
- `GET /api/v1/pricing` -- the v1 per-MTok pricing table. During the
  provider-neutral migration this shape can also be reconstructed from the v2
  rate cards (`services/pricing_adapter.py`) until Task 67 replaces the UI.
- `POST /api/v1/pricing/sync-litellm` -- fetch LiteLLM prices; also feeds the
  v2 `rate_cards` via the import (auto-apply, audited as `v1_sync`).

## API tokens

- `POST /api/v1/tokens` `{ "label": "openclaw" }` -> `201` with the plaintext
  token, shown only once.
- `GET /api/v1/tokens` -- token metadata (never the secrets).
- `DELETE /api/v1/tokens/{label}` -> `204`; revoked tokens are rejected.

Third-party apps (for example OpenClaw) use a minted token to read any query
endpoint -- the API exposes everything the dashboard shows.

## Live stream

- `WS /api/v1/stream?token=<token>` -- authenticated WebSocket; emits a JSON
  message per accepted ingest batch (`{"type": "events"|"limits", "machine",
  "accepted"}`). Best-effort live view; slow clients are dropped rather than
  blocking ingest.

## Registry (v2)

The provider and model registries are exposed under `/api/v2` (same bearer
auth). See [registries.md](registries.md) for the full contract.

- `GET /api/v2/providers` -- all provider registry metadata, including the
  `registered` flag for observed-but-unknown providers.
- `GET /api/v2/models` -- model registry rows, filterable by `provider` and
  `lifecycle`, each with its native id and alias spellings.

## Usage and cost (v2)

Provider-neutral read endpoints under `/api/v2` (scope `query:read`). Both take
a bounded `from`/`to` range (max `TOKEMETRY_QUERY_MAX_RANGE_DAYS`), the uniform
filters (`provider`, `model`, `source`, `machine`, `project`, `session`,
`environment`, `outcome`, plus `unknown_provider`/`unknown_model`), an explicit
`sort`, and a data-quality `warnings` envelope (unpriced events, unknown models,
stale sources).

- `GET /api/v2/usage?from&to&group_by` -- final-attempt usage grouped by a
  dimension (`day`, `provider`, `model`, `machine`, `project`, `source`,
  `environment`, `session`); returns all six token counters plus `attempt_count`
  (snapshots and logical-request summaries are excluded).
- `GET /api/v2/costs?from&to&group_by` -- `actual_spend_usd` and
  `subscription_value_usd` as separate series (never merged), a cost-status split
  (`cost_priced_usd`/`cost_partial_usd`/`cost_estimated_usd`,
  `unpriced_event_count`), and each row's `pricing_version` (`mixed` when it
  spans several).
- `GET /api/v2/costs/reconciliation?from&to` -- observed-versus-computed cost
  drift by provider (populated once exporters supply observed costs).

## Trace: attempts, requests, sessions (v2)

The trace surface under `/api/v2` (scope `query:read`); all take a bounded
`from`/`to` range and keyset pagination via an opaque `cursor` + `next_cursor`.

- `GET /api/v2/attempts?from&to` -- the raw, keyset-paginated, newest-first
  listing of final attempt events with their lifecycle and usage fields;
  filterable by the uniform filters plus `logical_request_id`.
- `GET /api/v2/requests?from&to` -- logical requests with their attempt-chain
  aggregates (attempt/fallback counts, winning attempt, token and cost totals
  computed from attempts); filterable by `routing_policy` and `fallback_only`.
- `GET /api/v2/requests/{provider}/{logical_request_id}` -- the drilldown: the
  request plus its attempts ordered by sequence for the fallback-chain UI.
- `GET /api/v2/sessions?from&to` and `GET /api/v2/sessions/{scoped_id}` --
  session rollups keyed by the scoped identity `(provider, source, session_id)`;
  the v1 sessions endpoints keep serving the old shape during the migration.

## Other v2 read resources

Read-only, keyset-paginated, scope `query:read`:

- `GET /api/v2/limits?from&to` -- limit-utilization snapshots with
  official/estimated provenance; filterable by `provider`, `machine`,
  `window_kind`, `provenance`.
- `GET /api/v2/data-quality` -- recorded anomalies filterable by `kind`,
  `subject`, `source`, and `resolved` state (feeds the data-quality UI).
- `GET /api/v2/pricing` -- the read-only rate-card listing (mutation is under
  the pricing-admin endpoints).
- `GET /api/v2/rollups?from&to` -- `daily_rollups` rows exposed directly for
  external tooling with the stable column contract.

## Pricing administration (v2)

The provider-neutral pricing surface is under `/api/v2/pricing`. Reads need
`query:read`; every mutation needs `admin:pricing`, is audited, and returns the
current pricing-state version. See
[architecture/pricing-v2.md](../architecture/pricing-v2.md) for the model.

- `GET /api/v2/pricing` -- list rate cards, filterable by `provider`,
  `native_model`, `unit_type`, and `active_on` date.
- `POST /api/v2/pricing` -- create a rate card (manual price or override);
  `400` on a same-grain date overlap.
- `POST /api/v2/pricing/{id}/close` -- close a rate card (`{ "effective_to" }`);
  `404` if unknown.
- `POST /api/v2/pricing/import?dry_run=true|false` -- diff a LiteLLM + curated
  price set (dry run returns the structured diff and a `digest`); apply with
  `dry_run=false` and that `digest` (`409` if the stored rates changed).
- `POST /api/v2/pricing/reprice` / `POST /api/v2/pricing/revert` -- recompute a
  time range under a new pricing version, retaining prior rows, or re-activate a
  named prior version.
- `GET /api/v2/pricing/reports/unpriced` -- unpriced/partial events by model.
- `GET /api/v2/pricing/reports/unknown-models` -- unknown-model observations.
