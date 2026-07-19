# Ingest API v2 (provider-neutral)

The v2 ingest surface accepts the provider-neutral usage event and its sibling
records. It runs alongside v1 for the whole migration (FR-INGEST-011/014): the
version is chosen by URL path (`/api/v2/*`), there is no content negotiation, and
existing v1 collectors keep working unchanged. This document is the exporter's
reference; the wire model itself is [usage-event-v2.md](../architecture/usage-event-v2.md)
and the ledger is [event-model-v2.md](../architecture/event-model-v2.md).

## Authentication and scopes

Every endpoint (except `GET /api/v2/ready`) requires a bearer token with the
right scope: `/ingest/events` and `/ingest/validate` need `ingest:events`,
`/ingest/limits` needs `ingest:limits`, `/ingest/aggregates` needs
`ingest:aggregates`, `/schemas/usage-event` needs `query:read`, and a batch with
`correction: true` also needs `admin:corrections`. A token missing a scope gets
`403`; ingest-only tokens therefore cannot query (FR-INGEST-004). The env
bootstrap token holds every scope. An optional per-token source allowlist
rejects (403, structured error) any event whose source name is not listed
(FR-INGEST-020). Authentication failures return a uniform `401` that never
reveals whether a token exists (FR-SEC-010). Ingest traffic is rate limited in a
class separate from query traffic (FR-INGEST-015); exceeding it returns `429`.
Every response carries an `X-Request-ID` (FR-INGEST-016), echoing a
client-supplied one when present.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/ingest/events` | Ingest a batch of attempt/summary events. |
| POST | `/api/v2/ingest/validate` | Schema + privacy pre-flight, no persistence. |
| POST | `/api/v2/ingest/limits` | Append provider-neutral limit snapshots. |
| POST | `/api/v2/ingest/aggregates` | Import historical daily aggregates. |
| GET | `/api/v2/schemas/usage-event` | The published usage-event JSON schema. |
| GET | `/api/v2/ready` | Unauthenticated readiness (DB + migration). |

## Batch envelope

```json
{
  "schema_version": 2,
  "events": [ /* UsageEventV2 objects, each carrying its own source */ ],
  "return_ids": false,
  "correction": false
}
```

`schema_version` must be `2`. Bodies may be gzip-compressed
(`Content-Encoding: gzip`, FR-INGEST-010). Limits are settings-driven
(FR-INGEST-005): `ingest_max_events` (default 1000) and `ingest_max_bytes`
(default 5 MiB); exceeding either returns `413`.

The `/limits` and `/aggregates` batches use the same envelope with a `snapshots`
or `aggregates` list respectively.

## Event lifecycle semantics

Each event is a `(provider, event_id)` with a `finality` and a `sequence`:

- **snapshot** -- an in-progress state (for example a streamed response). A
  higher `sequence` supersedes a lower one; the prior state is archived.
- **final** -- the terminal state. A final supersedes any snapshot; a later
  snapshot after a final is ignored.
- **correction** -- changing a final requires `"correction": true` (and the
  `admin:corrections` scope once Task 63 lands). The prior final is archived with
  the actor and reason.

The per-event outcome is one of `accepted`, `updated`, `duplicate`, `rejected`,
`corrected`; the response reports the five counts plus a server-generated
`batch_id` (FR-INGEST-008). Same-sequence conflicts and unauthorized
final-over-final changes are `rejected` and surfaced as data-quality events.

## Idempotency guidance for exporters

Ingest is idempotent by `event_id`. **Replaying a batch after an ambiguous
transport failure (timeout, dropped connection) is always safe:** an identical
event is a no-op `duplicate`, and a superseding snapshot/final resolves
deterministically. Exporters should retry with the same `event_id`s rather than
minting new ones, and may set `sequence` to order streamed snapshots. Batches
are transactional (FR-IDEMP-009): a structural failure (a schema or privacy
violation in `reject` mode) rejects the whole batch with no partial writes.

## Validation and errors

`POST /api/v2/ingest/validate` runs the same schema and privacy checks and
returns `{ "valid": bool, "errors": [...], "request_id": ... }` **without
persisting** (FR-INGEST-007), so an exporter can pre-flight a batch. On
`/events`, a validation failure returns `422` whose `detail` is the same
structured shape:

```json
{ "errors": [ { "index": 1, "field_path": "native_model", "code": "missing", "message": "Field required" } ],
  "request_id": "..." }
```

`index` is the event's batch position (`-1` for an envelope error); `field_path`
is a dotted path; `code`/`message` name the failure (FR-INGEST-006). Privacy
violations (a prohibited content-like key, a non-allowlisted dimension, a
disabled tool histogram) use the same shape.

## Schema and OpenAPI

`GET /api/v2/schemas/usage-event` serves the JSON Schema (2020-12) generated from
the same `UsageEventV2` model the endpoint validates against, so the published
schema can never drift from enforcement (FR-INGEST-012). The FastAPI OpenAPI
document (`/openapi.json`, `/docs`) describes the v2 paths, response models, and
the version-negotiation notes (FR-INGEST-011); client libraries are generated
from it in Task 65.

## Performance

On reference hardware a 100-event batch settles well under the 200 ms p95 budget
(NFR-PERF-001) and a 5000-event v1 compatibility batch (PRD 18.5) completes in a
single call. The `perf`-marked suite (`pytest -m perf`) records baselines; the
full 1000 events/s sustained gate (NFR-PERF-002) is owned by Task 70.
