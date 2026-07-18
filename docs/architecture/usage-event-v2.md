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
| FR-EVENT-020 bounded dimensions | Modeled (`dimensions`); bounds enforced in task 62.2. |
| FR-EVENT-021 no content fields | Implemented (asserted by test). |
| FR-EVENT-024 zero-token failed attempts | Implemented (all counters optional). |
| FR-EVENT-025 provenance values | Implemented (extended `Provenance`). |
| FR-OTEL-001 trace/span linkage | Implemented (`trace_id`/`span_id`/`parent_span_id`). |
| FR-INGEST-012 OpenAPI/JSON schema | Implemented (`usage_event_json_schema`); endpoint in task 62.12. |
