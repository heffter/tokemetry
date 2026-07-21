# OpenTelemetry GenAI mapping

The normative mapping from OpenTelemetry generative-AI span attributes to v2
attempt-event fields, used by the feature-flagged OTLP bridge (Epic TOK-11,
decision D-013).

## Pinned version

- **Semantic conventions:** `1.30.0` (constant `SEMCONV_VERSION` in
  `otel/semconv.py`). Verify against the opentelemetry.io semconv release notes
  when bumping. The GenAI conventions were still marked *development* upstream at
  this pin, so the bridge treats unknown attributes leniently.
- The pinned version is stamped on every mapped event as
  `extra.otel.semconv_version` (FR-OTEL-006), so a stored event always records
  which convention produced it.

## Attribute mapping

| Semconv attribute | v2 field | Notes |
|---|---|---|
| `gen_ai.system` | `provider` | Normalized to a provider id via the registry aliases |
| `gen_ai.request.model` | `requested_model` | |
| `gen_ai.response.model` | `native_model` | Falls back to the request model if absent |
| `gen_ai.operation.name` | (context) | e.g. `chat`, `text_completion`; recorded in `extra.otel` |
| `gen_ai.usage.input_tokens` | `input_tokens` | |
| `gen_ai.usage.output_tokens` | `output_tokens` | |
| `gen_ai.usage.cache_read_tokens` | `cache_read_tokens` | Provider extension; falls back to `extra.otel` if named differently |
| `gen_ai.usage.reasoning_tokens` | `reasoning_tokens` | Provider extension; `extra.otel` fallback |
| `error.type` + span status | `outcome`, `success` | Non-OK status or a present `error.type` -> `success=false`, `outcome=error` |
| span start time | `ts_started` | |
| span end time | `ts_completed` | |
| (span end - start) | `latency_ms` | Derived server-side latency |
| trace/span ids | `trace_id`, `span_id`, `parent_span_id` | W3C trace-context (Task 71.1) |

Any span attribute not in the table is retained under `extra.otel.<key>`, within
the standard metadata size/depth bounds, so nothing useful is lost while the
schema stays stable (FR-OTEL-002).

## Content attributes (stripped)

Never mapped and never stored (FR-OTEL-007), stripped before any event is built:

- `gen_ai.prompt`, `gen_ai.completion`
- `gen_ai.input.messages`, `gen_ai.output.messages`
- any attribute prefixed `gen_ai.prompt.` or `gen_ai.completion.`, and GenAI
  event bodies (prompt/completion log records)

## v2 concepts a span does not carry

Spans model one call, so span-derived events default:

- `event_kind = attempt`, `finality = final`, `sequence = 1` (one final attempt
  per span; no revision chain).
- `billing_mode` defaults to `api_billed` (the cost split, D-007, is applied
  downstream from the resolved source, not from the span).
- `provenance = imported`, `source.type = sdk` (the OTLP bridge is an SDK-class
  source), consistent for every span-derived event.
- `logical_request_id` is unset unless the instrumentation provides one; trace
  grouping (Task 71.1) links related spans instead.

## Testing

Each row of the attribute-mapping table becomes a one-to-one converter test case
in Task 71.4; the content-attribute list drives the stripping tests. A doc/code
consistency test asserts `SEMCONV_VERSION` and the mapping constants stay in sync
with this document.
