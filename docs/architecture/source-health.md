# Sources, health, and scoped tokens

Epic TOK-4 separates *who reported* usage (the source) from *where it ran* (the
machine), and locks down ingest with least-privilege tokens. This document is
the model reference; the APIs are [sources.md](../api/sources.md) and
[ingest-v2.md](../api/ingest-v2.md).

## Source model and identity

A source is a reporting agent: a `collector`, `gateway`, `sdk`, `importer`, or
`manual` actor (FR-SOURCE-002). Its identity is `(type, name, instance_id)` and
is **never** conflated with a machine (FR-SOURCE-003); one machine may host
several sources (FR-SOURCE-009). Sources auto-register from v2 ingest payloads on
first sight (D-011) and thereafter advance `last_seen` and `version`. A source
carries an optional `machine` link (FR-SOURCE-008 keeps the `machines` table
fully supported), a `token_label`, and a `billing_mode` (`api_billed` vs
`subscription`, D-007). Labels are mutable without changing event identity
(FR-SOURCE-010); revoking a source stops accepting its future events but never
deletes history (FR-SOURCE-012).

### v1 derived source

V1 collector payloads carry machine info but no source object. Each v1 batch is
attributed to a derived source `(collector, claude-code-collector, machine)`
with the version from the batch's `collector_version`. Historical rows from the
v1-to-v2 backfill are attributed by migration `0014`. The v1 `source` column
value (`"collector"`) is unchanged, so the compatibility view stays
byte-identical.

## Health

Health is computed at query time from stored fields (no background job). Each
batch updates:

| Field | Meaning |
|---|---|
| `last_successful_ingest` | Receipt time of the last error-free batch; drives staleness. |
| `recent_error_count` | Rolling count of rejected events within `source_error_window_seconds`. |
| `reported_schema_version` | The schema version of the last batch (1 for v1, 2 for v2). |
| `clock_skew_seconds` | Max event timestamp minus receipt time; positive means the source's clock is ahead. |

### Staleness

A source is stale when `last_successful_ingest` is older than the per-type
threshold (FR-SOURCE-005/006), or when it has never ingested:

| Type | Default threshold |
|---|---|
| `collector` | 30 minutes (`source_stale_collector_seconds`) |
| `gateway` | 10 minutes (`source_stale_gateway_seconds`) |
| others | 30 minutes (`source_stale_default_seconds`) |

The dashboard sources page (Task 67) and the `stale_source` alert (Task 68) read
this; the legacy machine-based `collector_stale` alert coexists on machines.

### Clock skew

When `abs(clock_skew_seconds)` exceeds `source_clock_skew_warn_seconds` (default
5 minutes) a `clock_skew` data-quality event is recorded (R-009 mitigation).
Timestamps beyond the bounded-future limit are rejected at validation.

## Token scopes

Bearer tokens hold a least-privilege scope set (FR-INGEST-003, D-002/D-015):

| Scope | Grants |
|---|---|
| `ingest:events` | v2 (and v1) event ingest. |
| `ingest:limits` | limit-snapshot ingest. |
| `ingest:aggregates` | historical aggregate import. |
| `query:read` | all read/query endpoints and the WebSocket stream. |
| `admin:tokens` | token and source administration. |
| `admin:corrections` | final-over-final event corrections. |
| `admin:pricing` | pricing administration (Task 64). |
| `admin:retention` | retention administration (Task 70). |

Least-privilege examples:

- **Collector token**: `["ingest:events", "ingest:limits"]` -- ingests events and
  limit snapshots, cannot query.
- **Gateway token** (aiProviderProxy): `["ingest:events"]`, usually with a source
  allowlist of the gateway's source name.
- **Dashboard token**: `["query:read"]` -- reads everything, ingests nothing.

An ingest-only token receives `403` on any query endpoint (FR-INGEST-004). The
env bootstrap token implicitly holds every scope (FR-SEC-008). Authentication
failures return a uniform `401` that never reveals whether a token exists
(FR-SEC-010).

### Source allowlists

A token may carry an optional `source_allowlist` (FR-INGEST-020, FR-SEC-004): a
v2 batch whose event source name is not on the list is rejected with `403` and a
structured error, so a gateway token cannot report for a source it does not own.

### Rotation runbook (FR-SEC-009)

Secrets never rotate in place; rotate by replace-then-revoke:

1. `POST /api/v1/tokens` with a new label and the same scopes/allowlist; record
   the one-time plaintext.
2. Deploy the new token to the client.
3. `DELETE /api/v1/tokens/{old-label}` once the client is confirmed switched.

Source history is unaffected because tokens are attributable to a source, not
the reverse.

## Requirement coverage

| Requirement | Status |
|---|---|
| FR-SOURCE-001 register sources | Implemented (`sources`, auto-registration). |
| FR-SOURCE-002 source types | Implemented (`type` enum-as-string). |
| FR-SOURCE-003 identity != machine | Implemented (`(type,name,instance_id)` grain). |
| FR-SOURCE-004 retained fields | Implemented (first/last seen, version, instance, machine, token label). |
| FR-SOURCE-005 health fields | Implemented (health columns + query-time compute). |
| FR-SOURCE-006 stale sources | Implemented (`GET /api/v2/sources?stale=`). |
| FR-SOURCE-007 stale alert | Deferred to Task 68 (health exposed here). |
| FR-SOURCE-008 machine table supported | Implemented (machine link; API unchanged). |
| FR-SOURCE-009 one machine many sources | Implemented (grain allows it). |
| FR-SOURCE-010 mutable labels | Implemented (`PATCH`; event identity fixed). |
| FR-SOURCE-011 tokens attributable to a source | Implemented (`token_label`, source allowlist). |
| FR-SOURCE-012 revoke keeps history | Implemented (`revoke`; rows retained). |
| FR-SEC-001/002/005/006 hashed tokens, one-time secret, revoke, last_used | Implemented (unchanged token security). |
| FR-SEC-003 scopes | Implemented (scope vocabulary + enforcement). |
| FR-SEC-004 source allowlist | Implemented. |
| FR-SEC-007 admin endpoints require admin scopes | Implemented (`admin:tokens`). |
| FR-SEC-008 bootstrap all scopes | Implemented. |
| FR-SEC-009 rotation | Documented (replace-then-revoke). |
| FR-SEC-010 uniform auth failure | Implemented (uniform 401). |
| FR-SEC-011 scope on WebSocket | Implemented (`query:read` on the stream). |
