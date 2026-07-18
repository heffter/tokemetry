# Event model v2: ledger, revisions, and logical requests

This is the map of the provider-neutral usage-event storage introduced by Epic
TOK-3. The wire model, privacy rules, ingest pipeline, and the v1 compatibility
view are detailed in [usage-event-v2.md](usage-event-v2.md) and
[database.md](database.md); this document ties the three storage tables together.

## The ledger: `usage_events_v2`

`usage_events_v2` holds the **active** (current) state of every event, keyed by
`(provider, event_id)`. Each row is one attempt of one request, flattened from
the v2 wire model: finality/sequence, separate requested/routed/native models,
six token counters plus reasoning, success/outcome, latency metrics, provenance,
a `source_id` (Task 63), the `routing`/`dimensions`/`extra`/`tool_histogram`
JSON, trace ids, and a transitional `cost_usd` column (removed when the cost
engine lands in Task 64). Only `event_kind = "attempt"` rows carry billable
usage (FR-EVENT-004); `logical_request`, `import`, and `adjustment` rows are
metadata.

The physical v1 `usage_events` table is now a read-only view over this ledger's
attempt rows, so v1 clients and Grafana see the exact v1 column shape while the
data lives in v2 (D-001). New `usage_events_v2` rows come only through the
revision engine.

## Revisions: `usage_event_revisions`

Because streamed responses arrive as snapshots and finality is settled later,
each `(provider, event_id)` can be revised. The **revision engine**
(`services/revisions.py`) resolves every incoming event against the active row:

- A brand-new id is **accepted**.
- A higher-sequence snapshot, or a final over a snapshot, **supersedes** the
  prior state (archived with reason `superseded`) -- outcome `updated`.
- A byte-identical replay, or a stale/out-of-order event, is a `duplicate` no-op.
- A same-sequence differing payload, or a final over a final without an
  authorized correction, is **rejected** and recorded as a `sequence_conflict`
  data-quality event.
- An authorized `correction` archives the prior final (reason `correction`, with
  actor and reason text) and writes the new one -- outcome `corrected`.

Every superseded or corrected state is written to `usage_event_revisions`
(sequence, finality, a `payload` snapshot, reason, actor, timestamp), giving each
event id a full, auditable history (FR-IDEMP-006). The `ConflictMode.KEEP_MAX`
mode reproduces the legacy v1 keep-maximum-output behavior exactly
(FR-IDEMP-012), so v1 ingest maps into the same ledger without changing v1 wire
behavior.

## Logical requests: `logical_requests`

A logical request groups the attempts of one upstream request -- retries and
provider fallbacks -- so the dashboard shows the whole cascade without
double-counting usage (D-003). `logical_requests` is keyed by
`(provider, logical_request_id)` and holds no usage of its own; it is
**recomputed** from the ledger (`services/logical_requests.py`) whenever an
attempt for it is ingested:

- `attempt_count` -- the number of attempt rows.
- `fallback_count` -- attempts whose `routing.fallback_from` is set.
- `winning_attempt_id` -- the successful final attempt (last completed on ties).
- `ts_first`/`ts_last`, requested model, and routing policy/reason.

Recomputing (rather than incrementing) keeps the summary correct under
out-of-order arrival, snapshot/final supersedes, replays, and corrections. A
`logical_request` summary event updates metadata only and never adds billable
usage (FR-EVENT-004, FR-TRACE-007).

## How the tables relate

```
                 ingest (v1 keep-max  or  v2 revision engine)
                                  |
                                  v
   usage_event_revisions  <--  usage_events_v2  -->  logical_requests
   (archived prior states)     (active attempt         (recomputed grouping,
                                & summary rows)          winning attempt)
                                  |
                                  v
                   usage_events  (read-only v1 view: attempts only)
```
