# Session analytics (provider-neutral)

How the v2 platform models per-session analytics and agent hierarchy, and the
decision on the v1 per-turn metrics (Task 75).

## Agent hierarchy (FR-TRACE-009)

Multi-agent workflows are reconstructed from the events, not a bespoke column:

- Every attempt carries `agent_id` (which agent made the call), now exposed on
  `GET /api/v2/attempts`.
- Parent-agent linkage is **derived from the OpenTelemetry span tree** (Task
  71.1): a call's parent agent is the agent of the attempt whose `span_id`
  matches this call's `parent_span_id`. No `parent_agent_id` column or collector
  change is required.
- `GET /api/v2/sessions/{scoped_id}/agents` returns the session's agent tree:
  each node has its `agent_id`, `parent_agent_id`, `depth` (0 for a root agent),
  and `attempt_count`, roots first, so the dashboard can indent or draw tree
  lines.

## Per-turn analytics: decision (Gap 2)

The v1 SessionsView exposed Claude-Code-specific per-turn metrics. Their fate in
a provider-neutral world:

| v1 metric | Decision | Rationale |
|---|---|---|
| `/clear` inflection points | **Retire** | `/clear` is a Claude-Code command with no provider-neutral analogue |
| Per-turn context growth | **Retire** | Modeled Claude's single-conversation context window; not meaningful across providers and multi-agent traces |
| Tokens per turn | **Keep, generalized** | Available from v2 attempt/session aggregates (attempt counts and token tiers) without a bespoke endpoint |
| Cache-hit ratio | **Keep, generalized** | `cache_read_tokens` vs total input is already served by the v2 usage/session queries |

The Claude-specific turn reconstruction is retired; the generalizable metrics
are computed from the existing v2 session and attempt surfaces (grouped usage,
session rollups, and the attempt list), so no provider-specific per-turn service
is carried into v2. Agent hierarchy replaces the Claude-only "sidechain" view as
the provider-neutral way to understand multi-step work.
