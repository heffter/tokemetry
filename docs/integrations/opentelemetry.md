# OpenTelemetry interoperability

tokemetry speaks OpenTelemetry two ways (Epic TOK-11): every v2 attempt carries
W3C **trace-context** ids so it lines up with your traces, and an optional
**OTLP receiver** turns instrumented GenAI spans into v2 attempt events. Both
are metadata-only -- prompt/response content is never stored.

## Trace-context on every event

`trace_id`, `span_id`, and `parent_span_id` are first-class fields on
`usage_events_v2` (FR-OTEL-001). Send them on any v2 event and:

- attempts sharing a `trace_id` form a **trace group** -- filter with
  `GET /api/v2/attempts?trace_id=<id>`;
- `parent_span_id` links a subagent attempt to its parent attempt's `span_id`,
  reconstructing agent hierarchies (FR-TRACE-008/009).

Ids are validated as W3C trace-context leniently: a non-conforming id is stored
as-is and raises a `trace_context_malformed` data-quality note, so nothing is
dropped.

## OTLP receiver (opt-in)

Disabled by default (decision D-009); core ingest never depends on it
(FR-OTEL-004). Enable it with:

```
TOKEMETRY_OTEL_RECEIVER_ENABLED=true
```

Point an OTLP/HTTP (JSON) trace exporter at:

```
POST /api/v2/otel/v1/traces
Authorization: Bearer <token with the ingest:events scope>
Content-Type: application/json
```

The receiver decodes the OTLP `ExportTraceServiceRequest`, converts each GenAI
span to a v2 attempt event, and ingests it through the normal pipeline (privacy
validation, source resolution, idempotency). Non-GenAI spans (no
`gen_ai.system`) are ignored. The response is the OTLP success shape.

> The receiver currently accepts the OTLP/HTTP **JSON** encoding. The protobuf
> encoding decodes behind the same boundary and can be added without changing
> the mapping or the endpoint.

### What is mapped

The span-to-event mapping is pinned to a specific semantic-convention version
and documented in full in
[otel-mapping.md](../architecture/otel-mapping.md): `gen_ai.system` to provider,
request/response model, token tiers, `error.type` and span status to
success/outcome, span times to `ts_started`/`ts_completed` with derived latency,
and trace-context ids. Every mapped event records the pinned version under
`extra.otel.semconv_version`, and unmapped attributes are retained under
`extra.otel` within the standard metadata bounds.

### Privacy

Content attributes (`gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`,
`gen_ai.output.messages`, and prompt/completion event bodies) are stripped
unconditionally before an event is built (FR-OTEL-007). Any other
content-looking attribute that reaches `extra.otel` is caught by the privacy
validator on ingest, exactly like a collector or proxy event.

## Not (yet) an OTel exporter

Exporting tokemetry data *to* an external OpenTelemetry backend is deliberately
deferred (FR-OTEL-005). The current scope is interoperability on the ingest
side: consuming GenAI spans and aligning on trace-context.
