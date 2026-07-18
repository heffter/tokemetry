# Usage Event v2 wire model

The v2 usage event is the provider-neutral record at the heart of Epic TOK-3.
It replaces the fixed, Anthropic-shaped v1 `UsageEvent` with an attempt-level
model that can represent streamed snapshots, retries and fallbacks, reasoning
tokens, nuanced terminal states, source identity, and OpenTelemetry trace
linkage. The wire shape follows PRD-TOK-002 Section 12.3.

The single definition is a frozen `UsageEventV2` pydantic model in
`tokemetry_core.usage_v2`, shared verbatim by exporters, the collector, and the
server so both sides of the wire validate against the same contract. Every
model in the module is `frozen=True` (events are immutable through the pipeline)
and `extra=forbid` (unexpected fields fail loudly in tests rather than being
silently dropped).

This document covers the wire shape and per-field invariants (task 62.1). The
privacy validation layer (prohibited content-like keys, dimension bounds,
size/depth limits) is task 62.2; the storage ledger, revision engine, and
ingest API are tasks 62.3-62.6.

## Field groups

| Group | Fields | Notes |
|---|---|---|
| Identity and lifecycle | `schema_version` (literal `2`), `event_id`, `event_kind`, `finality`, `sequence` | `schema_version` is required (FR-EVENT-001); `event_id` is unique within the canonical provider namespace (FR-EVENT-002). |
| Correlation | `logical_request_id`, `attempt_id`, `provider_request_id`, `provider_response_id` | Group retries/fallbacks (FR-EVENT-009/010) and link provider-side ids (FR-EVENT-011); all optional. |
| Models | `provider`, `native_model`, `requested_model`, `routed_model` | Requested, routed, and native identifiers are kept separate (FR-EVENT-012); `provider` and `native_model` are required. |
| Timestamps | `ts_started`, `ts_first_token`, `ts_completed` | All timezone-aware (FR-EVENT-013); only `ts_started` is required so failed attempts need no completion time. |
| Attribution | `machine`, `project`, `session_id`, `agent_id`, `environment` | Query dimensions; all optional. |
| Token counters | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_short_tokens`, `cache_write_long_tokens`, `reasoning_tokens` | Non-negative, default `0` (FR-EVENT-014); reasoning is stored apart from visible output (FR-EVENT-015). |
| Outcome | `success`, `outcome`, `http_status`, `stop_reason`, `service_tier`, `streaming` | `success` (did it succeed) is separate from `outcome` (nuanced terminal state) per FR-EVENT-017. |
| Performance | `latency_ms`, `time_to_first_token_ms`, `tool_call_count`, `tool_histogram` | Non-negative; `tool_histogram` is optional and gated server-side (default off, task 62.2). |
| Provenance and source | `provenance`, `source` | `source` is required (D-011): every v2 event carries source identity (FR-EVENT-018). |
| Extensions | `routing`, `dimensions`, `extra` | Gateway-neutral routing (FR-EVENT-019); bounded dimensions (FR-EVENT-020); provider/gateway-namespaced `extra` (FR-PROVIDER-006, FR-EVENT-016). |
| Tracing | `trace_id`, `span_id`, `parent_span_id` | OpenTelemetry linkage (FR-OTEL-001). |

## Enumerations

- `event_kind` (`EventKind`): `attempt`, `logical_request`, `import`,
  `adjustment` (FR-EVENT-003). Only `attempt` events carry billable usage
  (FR-EVENT-004).
- `finality` (`Finality`): `snapshot` (partial, still changing) or `final`
  (terminal). A `final` supersedes any `snapshot`; a `snapshot` never
  supersedes a `final` without an explicit correction (FR-IDEMP-004/005). The
  revision engine (task 62.4) enforces this.
- `source.type` (`SourceType`): `collector`, `gateway`, `sdk`, `importer`,
  `manual` (FR-SOURCE-002). Source identity is distinct from machine identity
  (FR-SOURCE-003).
- `provenance` (`Provenance`, extended for v2): `official`, `local_estimate`,
  `stats_cache`, `imported`, `adjusted` (FR-EVENT-025). The last two are new in
  v2; the first three keep their v1 meaning.

## Invariants

- `schema_version` must equal `2` and is required (FR-EVENT-001).
- `sequence` is a non-negative integer (FR-EVENT-006).
- All present timestamps are timezone-aware; naive datetimes are rejected
  (FR-EVENT-013), reusing the v1 `_require_tz` validator.
- Token counters, `latency_ms`, `time_to_first_token_ms`, and `tool_call_count`
  are non-negative.
- Failed and cancelled attempts are ingestible with every counter at zero
  (FR-EVENT-024): `success` defaults to `False` and no counter is mandatory.
- No content field exists anywhere in the schema (FR-EVENT-021): there is no
  place to put a prompt, completion, tool argument, file path, or reasoning
  text. A test asserts the property set contains no content-like key.

## Published JSON schema

`usage_event_json_schema()` returns the JSON Schema (2020-12) document for
`UsageEventV2`, generated directly from the model so the served schema and the
model validated at ingest can never drift. It adds a stable `title`, the
`$schema` dialect, and an `x-tokemetry-schema-version` marker. The
`GET /api/v2/schemas/usage-event` endpoint (task 62.12) serves exactly this
document, and OpenAPI (FR-INGEST-012) is derived from the same model. A unit
test pins the schema's property and required-field sets so any wire change is
deliberate.

## Requirement coverage (task 62.1)

| Requirement | Status |
|---|---|
| FR-EVENT-001 `schema_version` required | Implemented (`Literal[2]`, no default). |
| FR-EVENT-002 event id unique per provider | Modeled (`event_id`); uniqueness enforced by the ledger (task 62.3). |
| FR-EVENT-003 event kinds | Implemented (`EventKind`). |
| FR-EVENT-005 finality snapshot/final | Implemented (`Finality`). |
| FR-EVENT-006 non-negative sequence | Implemented (`sequence` `ge=0`). |
| FR-EVENT-009/010/011 correlation ids | Implemented (optional id fields). |
| FR-EVENT-012 separate model identifiers | Implemented (`requested`/`routed`/`native`). |
| FR-EVENT-013 tz-aware timestamps | Implemented (validators). |
| FR-EVENT-014 non-negative counters | Implemented (`ge=0` defaults). |
| FR-EVENT-015 reasoning kept separate | Implemented (`reasoning_tokens`). |
| FR-EVENT-016 unknown counters retained | Implemented (`extra`). |
| FR-EVENT-017 success and outcome separate | Implemented. |
| FR-EVENT-018 source identity fields | Implemented (`SourceRef`, required). |
| FR-EVENT-019 optional gateway-neutral routing | Implemented (`Routing`). |
| FR-EVENT-020 bounded dimensions | Implemented (allowlist + bounds in `services/privacy.py`). |
| FR-EVENT-021 no content fields | Implemented (asserted by test). |
| FR-EVENT-024 zero-token failed attempts | Implemented (all counters optional). |
| FR-EVENT-025 provenance values | Implemented (extended `Provenance`). |
| FR-OTEL-001 trace/span linkage | Implemented (`trace_id`/`span_id`/`parent_span_id`). |
| FR-INGEST-012 OpenAPI/JSON schema | Implemented (`usage_event_json_schema`); endpoint in task 62.12. |

## Privacy validation (task 62.2)

`tokemetry_server.services.privacy` is the strict server-side gate that keeps
the content-free guarantee (FR-EVENT-022, FR-PRIV-001/002). `PrivacyValidator`
takes a `PrivacyPolicy` and returns a `PrivacyResult` (a possibly-cleaned event,
a tuple of fatal `ValidationIssue` records, and the list of stripped paths).
Each open container has one dedicated control:

| Container | Control | Requirement |
|---|---|---|
| `extra` | Recursive content-key scan; top-level keys bounded to the event provider plus allowed namespaces (default `gateway`). | FR-EVENT-022, FR-PROVIDER-006 |
| `dimensions` | Allowlist (default `team`, `cost_center`, `environment`) plus bounds: 16 keys, 64-char keys, 256-char values. | D-004, FR-EVENT-020 |
| `tool_histogram` | Gated by `tool_names_enabled` (default off); when on, bounded to 32 names of 64 chars with non-negative counts. | D-005 |
| `routing` | Fixed-schema model (`extra=forbid`); no free-form keys to scan. | FR-EVENT-019 |
| whole event | Maximum serialized size (default 32 KiB) and JSON depth (default 8). | FR-EVENT-028, NFR-SEC-004 |

The content-key scan flags any key whose alphanumeric-only, lowercased form
contains a content token (`prompt`, `response`, `message`, `content`, `text`,
`arguments`, `path`, `code`, `snippet`, `completion`, `body`). Matching is
deliberately aggressive -- a benign field that merely contains one of these is
rejected rather than risk leaking content. Under `mode="reject"` (default) each
hit is a fatal issue; under `mode="strip"` the key is removed and the event
rebuilt. A seeded fuzz test injects forbidden keys at random depths and asserts
they are always caught or stripped (AC-011, AC-022). Settings-to-policy wiring
lands with the ingest endpoints (task 62.6).

## Revision engine (task 62.4)

`tokemetry_server.services.revisions` resolves each incoming event against the
active `usage_events_v2` row for its `(provider, event_id)` (PRD Section 12.4).
The decision logic is the pure `resolve(incoming, existing, mode, correction)`,
so the full `existing x incoming` matrix is unit-tested without a database;
`RevisionEngine.apply` reads the active row, calls `resolve`, and applies the
result inside the caller's transaction. Every event resolves to one outcome:

| Outcome | When | Effect |
|---|---|---|
| `accepted` | New event id. | Insert the active row. |
| `updated` | Higher-sequence snapshot, or a final over a snapshot. | Archive the prior state (`superseded`), write the new state. |
| `duplicate` | Byte-identical replay (FR-IDEMP-007), or a stale/out-of-order event -- a later snapshot after a final (FR-EVENT-008) or an older snapshot. | No-op. |
| `rejected` | Same sequence with a differing payload (FR-IDEMP-008), or a final over a final without an authorized correction (FR-IDEMP-005). | No write; record a `sequence_conflict` data-quality event. |
| `corrected` | Final over a final with the correction flag. | Archive the prior final (`correction`, actor, reason text), write the new final. |

Identity for the "identical replay" test is a `row_fingerprint`: a canonical,
key-sorted JSON string of the projected ledger row with timestamps normalized to
UTC, so a tz-aware incoming row and the naive row SQLite reads back compare
equal. Superseded and corrected prior states are archived to
`usage_event_revisions` with the reason, actor, timestamp, and a `previous`
snapshot of the row, giving each event id a full audit trail (FR-IDEMP-006).

`ConflictMode.KEEP_MAX` reproduces the legacy v1 keep-maximum-output resolution
exactly (FR-IDEMP-012), archiving nothing, so v1 traffic mapped into the ledger
(task 62.9) stays wire-compatible. The `admin:corrections` scope required for a
correction is enforced at the API boundary (task 62.6); `resolve` receives an
already-validated `correction` flag. The shared `usage_event_v2_row` projection
is reused by the ingest service (62.5) and the v1 mapper (62.9) so every write
path produces an identical row shape.

## Batch ingest (task 62.5)

`tokemetry_server.services.ingest_v2.IngestV2Service` turns a schema-valid batch
into ledger writes inside the caller's single transaction (FR-IDEMP-009). It
runs the privacy validator over every event first: in `reject` mode any
violation is a **structural** failure that raises `BatchValidationError` -- a
list of `BatchIssue(index, field_path, code, message)` (FR-INGEST-006) -- before
anything is written, so the batch is all-or-nothing. It then resolves each event
through the revision engine, accumulating the five outcome counts. A per-event
`sequence_conflict` is counted as `rejected` and recorded as a data-quality
event but does **not** fail the batch; only a structural failure or a database
error rolls it back.

Each batch writes one `ingest_batches` row with a server-generated `batch_id`
(FR-INGEST-008), the source identity, token label, counts, and `request_id`.
On request the service echoes the accepted/updated event ids, capped to a
configurable limit with an `ids_truncated` flag (FR-INGEST-009). The service is
transport-agnostic and never commits: the route (task 62.6) owns the transaction
and does the post-commit WebSocket publish, so a publish failure can never roll
back accepted ingest (NFR-REL-008). A request-id middleware stamps every
response with an `X-Request-ID` (FR-INGEST-016), honoring a client-supplied one.

## V1 ingest repoint (task 62.9)

The v1 ingest path (`services/ingest.py`) now mirrors every batch into the v2
ledger: after the existing keep-max upsert to the physical `usage_events`
table, each deduped event is mapped to a `UsageEventV2` (`event_kind='attempt'`,
`finality='final'`, `sequence=0`, v1-only fields under `extra['_v1']`, a
synthesized collector source) and applied through the revision engine in
`ConflictMode.KEEP_MAX` (FR-IDEMP-012). The keep-max cost is written to a
**transitional** `cost_usd` column on `usage_events_v2` (migration `0009`;
backfilled rows are populated from `extra['_v1']['cost_usd']`), which the v1
compatibility view will expose until cost moves to `computed_costs` (Task 64
drops the column).

The physical `usage_events` table stays the read source in this subtask, so
every v1 ingest and query response is byte-identical (verified by the Task 60.4
golden suite) and in-batch dedupe counts are unchanged. Swapping reads onto the
v1-shaped compatibility view over `usage_events_v2` -- the highest-risk,
cross-dialect step -- is isolated in subtask 62.10, gated on the backfill
verification passing.

## Logical requests (task 62.11)

`tokemetry_server.services.logical_requests` maintains the `logical_requests`
grouping (D-003, FR-TRACE-001/002). After the v2 ingest service applies a batch,
it recomputes each touched `(provider, logical_request_id)` from the current
ledger rows -- `LogicalRequestService.recompute` derives `attempt_count`,
`fallback_count` (attempts whose `routing.fallback_from` is set), `ts_first`/
`ts_last`, the requested model and routing policy/reason (from the summary event
if present, else the earliest attempt), and `winning_attempt_id` (the successful
final attempt, last completed on ties, FR-TRACE-004). Recomputing rather than
incrementing makes it correct under out-of-order arrival, snapshot/final
supersedes, replays, and corrections.

Only `attempt` events count toward the aggregates and usage; a `logical_request`
summary event updates metadata only and never adds billable usage (FR-EVENT-004,
FR-TRACE-007) -- and because the compatibility view and rollups already filter to
attempts, summary rows never affect token or cost sums (FR-TRACE-003/005).
