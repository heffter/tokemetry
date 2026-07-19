# Query API v2 (provider-neutral read surface, TOK-7)

The `/api/v2` read endpoints serve usage, cost, trace, and operational data over
the provider-neutral ledger and rollups. Every endpoint takes a bearer token
with scope `query:read`, a bounded time range, the uniform filters, and returns
`extra="forbid"` response models. This is the canonical reference; the
per-endpoint list is in [query.md](query.md).

## Resources

| Endpoint | Backing | Shape |
|---|---|---|
| `GET /usage` | ledger (final attempts) | grouped tokens + attempt count |
| `GET /costs` | active `computed_costs` | dual metrics + status split + pricing version |
| `GET /costs/reconciliation` | `computed_costs` | observed-vs-computed drift |
| `GET /attempts` | ledger | keyset-paginated raw attempts |
| `GET /requests` (+ drilldown) | `logical_requests` + attempts | attempt-chain aggregates |
| `GET /sessions` (+ `/{scoped_id}`) | ledger | session rollups by scoped identity |
| `GET /limits` | `limit_snapshots` | utilization with provenance |
| `GET /data-quality` | `data_quality_events` | anomalies |
| `GET /pricing` | `rate_cards` | read-only rate-card listing |
| `GET /rollups` | `daily_rollups` | rows for external tooling |

## Filters (FR-QUERY-002/011)

The uniform filter surface: `provider`, `model`, `source`, `machine`, `project`,
`session`, `environment`, `outcome`, plus the pseudo-filters `unknown_provider`
and `unknown_model` (events whose provider/model is not in the registry). The
aggregate endpoints also take `group_by`; the trace endpoints take resource
filters (`logical_request_id`, `routing_policy`, `fallback_only`).

## Pagination, sort, grain, bounds (FR-QUERY-003/004/005, NFR-PERF-004)

- **Keyset pagination** on the raw/listing endpoints (`attempts`, `requests`,
  `sessions`, `limits`, `data-quality`, `rollups`): an opaque `cursor` and a
  `next_cursor` in the response. The keyset is `(sort, id)`, stable under
  concurrent inserts (no skips or duplicates).
- **Sort** (`sort=field` or `-field`) is validated against a per-resource
  whitelist with a documented default; the aggregate endpoints sort in memory.
- **Grain** (`day`/`week`/`month`) truncates day-grouped aggregates.
- **Range bounds**: raw event queries reject a span wider than
  `TOKEMETRY_QUERY_MAX_RANGE_DAYS`.

## Warning envelope (FR-QUERY-010)

`/usage` and `/costs` responses carry a `warnings` list when the queried range
contains unpriced/partial events, unknown-model observations, or stale sources,
so a dashboard can flag incomplete data without a separate call.

## CSV export (FR-QUERY-009)

`/usage`, `/costs`, `/attempts`, and `/rollups` accept `format=csv`, streaming
RFC 4180 output with a stable header row (the aggregate endpoints stream their
grouped rows; the paginated ones stream the fetched page). Size is capped by the
same range bounds.

## Attempts vs. logical requests (FR-QUERY-008, FR-TRACE-007/012)

An **attempt** is one physical call to a provider; a **logical request** is the
user-facing request that may span several attempts when routing falls back. Only
final attempts are counted, never snapshots or logical-request summaries, so
usage and cost are never double counted across the fallback chain.

Worked fallback example: a request routed to `claude-opus-4-6` that rate-limits
and falls back to `claude-sonnet-4-5` produces two attempts under one
`logical_request_id` (`attempt_count=2`, `fallback_count=1`, `winning_attempt_id`
= the sonnet attempt). `GET /requests/{provider}/{logical_request_id}` returns
both attempts ordered by sequence; `GET /attempts?logical_request_id=...` lists
them raw. Token and cost totals for the request are summed from its attempts.

## v1 parity

The v1 `/api/v1` query endpoints are unchanged (the golden suite asserts them
byte-identical), and the same data queried through v1 and v2 agrees on totals
(`tests/integration/test_query_parity_v1_v2.py`). The v1 per-MTok pricing shape
can be reconstructed from the v2 rate cards (`services/pricing_adapter.py`) until
Task 67 replaces the UI.

## Requirements map (TOK-7)

| Requirement | Where |
|---|---|
| FR-ROLLUP-001/002/003 final attempts only, no snapshots/logical | `services/rollups.py` |
| FR-ROLLUP-004/005 provider-neutral grain | `daily_rollups` migration 0019 |
| FR-ROLLUP-007 cost split by status | rollup service + query |
| FR-ROLLUP-008/009 idempotent, correction/reprice-triggered refresh | `rollups`, `repricing` |
| FR-ROLLUP-011 stable Grafana views | migrations 0019/0020 |
| FR-QUERY-002/011 uniform + pseudo filters | `query_framework`, `queries_v2` |
| FR-QUERY-003 keyset pagination | `query_framework` |
| FR-QUERY-004/005 sort, grain | `query_framework` |
| FR-QUERY-006 pricing version (mixed) | `queries_v2.grouped_costs` |
| FR-QUERY-007/008 attempts, no double counting | `queries_v2`, parity suite |
| FR-QUERY-009 CSV export | `csv_export` |
| FR-QUERY-010 warning envelope | `query_framework.collect_warnings` |
| FR-QUERY-012 v1 parity | golden suite + parity suite |
| FR-QUERY-014 Grafana views | migration 0020 |
| FR-COST-012 dual metrics never merged | `queries_v2`, `cost_queries` |
| FR-LIMIT-004 limit provenance | `resource_queries.list_limits` |
| NFR-PERF-003/004 bounded, indexed | benchmark harness, migration 0021 |
