# Multi-provider limits (TOK-12)

Provider-neutral subscription and rate-limit visibility for Anthropic,
OpenAI/Codex, and Z.ai on one schema (FR-LIMIT-001..013, decision D-008).
Window kinds stay opaque provider-defined identifiers in storage, so a new
provider or a new window needs no migration (FR-LIMIT-001/009).

## Window registry (FR-LIMIT-012)

Each provider declares its limit windows in the core registry
(`ProviderDescriptor.windows`, `packages/core`): a `kind` (the opaque storage
label), a display `label`, `period_kind` (`rolling` / `calendar` / `opaque`)
with an optional `period_seconds`, and a `sort_order`. The descriptors seed the
`providers.windows` JSON column and are exposed through `GET /api/v2/providers`,
so dashboards and alerts resolve labels dynamically instead of hardcoding
`five_hour` / `seven_day`. Anthropic seeds its four OAuth windows (with the
labels the dashboard previously hardcoded, a zero-visual-change swap) plus the
gateway `requests_per_minute` / `tokens_per_minute` API rate-limit windows;
OpenAI seeds `primary` / `secondary`; Z.ai seeds `prompt_5h`. An unknown kind
has no descriptor and falls back to its raw kind.

## Snapshot dimensions (FR-LIMIT-002/003)

`limit_snapshots` carries the utilization reading plus, as first-class nullable
columns (migration 0023): `account`, `organization`, `source_id`,
`limit_amount`, `remaining`, `unit`. v1 and dimension-less snapshots leave them
null. Every reading keeps its `provenance` so official (collector) and estimated
(gateway) readings stay distinguishable in every query (FR-LIMIT-004).

## Stream grouping (FR-LIMIT-005)

A limit **stream** is keyed by
`(provider, window_kind, account, organization, source_id)`
(`services/limit_grouping`). Different keys are never merged into one series
without an explicit configured rule, so two accounts' identically-named windows
-- or a gateway-observed stream and a collector-official stream for the same
provider/window -- stay separate. Provenance is deliberately not part of the
key, so one stream can carry both an official reading and an estimate over time.

## Sources

- **Collector** limit sources (`apps/collector`) poll each provider's
  undocumented endpoint locally, read credentials that never leave the machine,
  and degrade to `LimitsUnavailableError` on any failure (the server records a
  `limit_source_failure` data-quality event, FR-LIMIT-013):
  `AnthropicOAuthLimitsSource`, `OpenAICodexLimitsSource` (`openai_codex`, off by
  default), `ZaiCodingLimitsSource` (`zai_coding_plan`, off by default).
- **Gateways** submit observed rate-limit headers as snapshots (provenance
  `estimated`) through `POST /api/v2/ingest/limits`; per-source per-window flood
  control caps how many are accepted per interval. See
  [../api/gateway-limits.md](../api/gateway-limits.md).

## Forecasting (FR-LIMIT-008)

`services/limit_forecast` forecasts each stream independently from its recent
utilization slope. A forecast identifies its source stream, a confidence tier
(`high` / `medium` / `low` / `unavailable`, from sample density and reading
span), and is period-aware (`will_reset_first` when the window resets before the
extrapolated exhaustion). Too little history yields `unavailable` rather than a
guess. Exposed at `GET /api/v2/limits/forecast`. The v1 `/summary/now`
prediction is unchanged.
