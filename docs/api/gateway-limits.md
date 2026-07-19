# Gateway rate-limit snapshots (FR-LIMIT-010)

A gateway or proxy in front of a provider sees the provider's own rate-limit
response headers on every call. It can turn those point-in-time observations
into limit snapshots and submit them to `POST /api/v2/ingest/limits` (scope
`ingest:limits`), so the dashboard shows API rate-limit pressure alongside the
collector's subscription-window readings.

These are **observations, not authoritative readings**, so they are recorded
with `provenance = "estimated"` and stay distinct from the collector's
`official` streams (see [Source identity](#source-identity)).

## Header mapping

Map the provider's rate-limit header family to one `LimitSnapshotV2` per window
kind. The two common families:

| Provider header | Window kind | Snapshot field |
|---|---|---|
| `anthropic-ratelimit-requests-limit` | `requests_per_minute` | `limit_amount` |
| `anthropic-ratelimit-requests-remaining` | `requests_per_minute` | `remaining` |
| `anthropic-ratelimit-requests-reset` | `requests_per_minute` | `resets_at` |
| `anthropic-ratelimit-tokens-limit` | `tokens_per_minute` | `limit_amount` |
| `anthropic-ratelimit-tokens-remaining` | `tokens_per_minute` | `remaining` |
| `anthropic-ratelimit-tokens-reset` | `tokens_per_minute` | `resets_at` |
| `x-ratelimit-limit-requests` | `requests_per_minute` | `limit_amount` |
| `x-ratelimit-remaining-requests` | `requests_per_minute` | `remaining` |
| `x-ratelimit-reset-requests` | `requests_per_minute` | `resets_at` |
| `x-ratelimit-limit-tokens` | `tokens_per_minute` | `limit_amount` |
| `x-ratelimit-remaining-tokens` | `tokens_per_minute` | `remaining` |
| `x-ratelimit-reset-tokens` | `tokens_per_minute` | `resets_at` |

`utilization_pct` is derived: `100 * (1 - remaining / limit_amount)` when both
are present. `unit` is `requests` or `tokens`. The window kinds
(`requests_per_minute`, `tokens_per_minute`) carry registry labels
("Requests / min", "Tokens / min"), so no dashboard change is needed for a new
gateway (FR-LIMIT-012). Unknown window kinds still ingest and render by their
raw kind (FR-LIMIT-009).

## Example snapshot

```json
{
  "schema_version": 2,
  "provider": "anthropic",
  "window_kind": "requests_per_minute",
  "ts": "2026-07-10T12:00:00Z",
  "utilization_pct": 50.0,
  "limit_amount": 1000,
  "remaining": 500,
  "unit": "requests",
  "resets_at": "2026-07-10T12:01:00Z",
  "provenance": "local_estimate",
  "source": { "type": "gateway", "name": "wg-proxy-1", "version": "1.0" }
}
```

## Source identity

Set the `source` reference so the gateway's stream is attributed to a distinct
source. Streams are keyed by `(provider, window_kind, account, organization,
source_id)` and are **never merged** without an explicit rule (FR-LIMIT-005), so
a gateway's `requests_per_minute` observations never blend into a collector's
subscription-window readings for the same provider.

## Flood control

A busy gateway would otherwise write a snapshot per request. The server accepts
**at most one snapshot per source per window kind per
`TOKEMETRY_LIMIT_SNAPSHOT_MIN_INTERVAL_SECONDS`** (default `0` = disabled;
gateway deployments should set it, e.g. `60`). Snapshots arriving inside the
interval for the same stream are silently dropped, so `accepted` in the response
may be less than the number submitted. Different window kinds and different
sources each get their own budget.
