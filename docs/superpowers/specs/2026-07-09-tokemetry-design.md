# tokemetry — Design Specification

Date: 2026-07-09
Status: Approved

## 1. Problem and goals

Claude Code is used on multiple machines (Windows, Linux, macOS) under a
Max/Pro subscription. Token usage is invisible across the fleet: local
transcripts are deleted after roughly 30 days, no existing tool aggregates
usage across machines, and the Anthropic Admin API is unavailable to
individual subscription accounts.

tokemetry provides:

- Durable, fleet-wide usage history (survives local transcript cleanup).
- Live subscription-limit monitoring: 5-hour block and weekly cap
  utilization, reset countdowns, burn-rate predictions.
- Retrospective analytics: per-project, per-machine, per-model, per-session
  breakdowns, cache efficiency, equivalent API cost as a value metric.
- A complete REST API so third-party applications (for example OpenClaw) can
  consume everything the dashboard shows.
- Active alerting (ntfy, Telegram, SMTP).

Non-goals for v1: OpenAI provider adapters (the abstractions ship in v1, the
concrete adapters later), OTEL ingest, Grafana dashboard JSON, statusline
integration, multi-user support.

## 2. Architecture

```
Machine A/B/C (Win/Linux/macOS)          VPS (WireGuard-only)
+---------------------------+          +----------------------------------+
| collector (Python daemon) |  HTTPS   | FastAPI service                  |
|  - JSONL watcher/tailer   |--------->|  - ingest API (idempotent)       |
|  - stats-cache bootstrap  |   WG     |  - Postgres                      |
|  - OAuth limits poller    |          |  - block/burn/cost engines       |
|  - SQLite offline queue   |          |  - alert engine -> ntfy/TG/SMTP  |
+---------------------------+          |  - REST API + WebSocket + SPA    |
                                       +----------------------------------+
```

Stack: Python 3.12 (uv workspace), FastAPI, SQLAlchemy 2 + Alembic,
Postgres (SQLite for development), Vue 3 + TypeScript + ECharts SPA.
The server binds to the WireGuard interface only; all API access requires a
bearer token.

## 3. Multi-provider OOP core (`tokemetry_core`)

All provider-specific knowledge lives behind three abstract interfaces. The
rest of the system works only with normalized types and a `provider`
discriminator. Adding a provider means implementing three classes and
registering them; core, server, API, and dashboard remain untouched.

- `UsageSource` (collector side): `discover()`, `parse(file, offset)`,
  `bootstrap()`. v1 concrete: `ClaudeCodeJsonlSource` (transcripts, subagent
  transcripts, stats-cache bootstrap). Future: `OpenAICodexSource`,
  `OpenAIApiSource`.
- `LimitsSource` (collector side): `poll() -> LimitSnapshot | None`.
  v1 concrete: `AnthropicOAuthLimitsSource`. Future: OpenAI credit/rate-limit
  sources.
- `PricingStrategy` (server side): `cost(event, price_row) -> Decimal`.
  v1 concrete: `AnthropicPricingStrategy` (cache multipliers 1.25x 5-minute
  write, 2x 1-hour write, 0.1x read). Future: `OpenAIPricingStrategy`.
- Normalized `UsageEvent` model: `provider`, `native_model`, generic token
  fields (`input`, `output`, `cache_read`, `cache_write_short`,
  `cache_write_long`), `extra: dict` for provider-specific counters
  (Anthropic web search/fetch requests). Anthropic 5m/1h cache TTLs map to
  short/long.
- Provider registry (plugin pattern): collector config enables sources per
  machine; the server resolves pricing strategies by `provider`.
- A `FakeProvider` test implementation ships in the v1 test suite to prove
  the interfaces and keep provider-specific assumptions out of core.
- `limit_snapshots` stores normalized `(provider, window_kind,
  utilization_pct, resets_at)` rows plus raw JSON so non-Anthropic quota
  models fit without migration.

## 4. Data sources (Claude Code, v1)

- JSONL transcripts: `~/.claude/projects/<encoded-cwd>/<session>.jsonl` plus
  `<session>/subagents/*.jsonl`; honor `CLAUDE_CONFIG_DIR`. Assistant lines
  carry `requestId`, `message.model`, `message.usage` (input/output tokens,
  `cache_read_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`,
  `cache_creation.ephemeral_1h_input_tokens`,
  `server_tool_use.web_search_requests`, `service_tier`, `speed`), plus
  top-level `sessionId`, `timestamp`, `cwd`, `gitBranch`, `version`, `slug`,
  `isSidechain`, `sessionKind`, `entrypoint`.
- Deduplication: one logical request emits 2-10 JSONL lines sharing a
  `requestId`; keep the maximum-usage entry per `requestId`. `input_tokens`
  can be a streaming placeholder; cache token fields are the most reliable.
- Cost: transcripts carry no `costUSD`; USD is computed server-side from a
  date-versioned pricing table synced from LiteLLM's
  `model_prices_and_context_window.json`, with manual overrides. Unknown
  models raise an alert instead of silently pricing at zero.
- Limits: undocumented `GET https://api.anthropic.com/api/oauth/usage`
  (bearer token from `~/.claude/.credentials.json`, header
  `anthropic-beta: oauth-2025-04-20`, Claude-Code-like User-Agent). Returns
  5-hour, 7-day, and per-model weekly utilization with reset times; it is
  authoritative and cross-device. Treated as a degradable source: on failure
  the system falls back to locally estimated limits. The OAuth token never
  leaves the machine.
- History bootstrap: `~/.claude/stats-cache.json` daily aggregates imported
  once per machine with `provenance=stats_cache`.
- Provenance tags on every number: `official` (OAuth endpoint),
  `local_estimate` (JSONL-derived), `stats_cache` (bootstrap).
- Privacy: conversation content never leaves the machine; only usage
  metadata and counters are collected.

## 5. Database (Postgres, Grafana-friendly plain tables)

- `machines(id, name, platform, first_seen, last_seen, collector_version)`
- `usage_events(event_id PK, provider, machine_id, session_id, ts, model,
  project, git_branch, client_version, entrypoint, is_sidechain,
  session_kind, input_tokens, output_tokens, cache_read_tokens,
  cache_write_short_tokens, cache_write_long_tokens, service_tier, speed,
  cost_usd, provenance, source, extra jsonb)` — idempotent upsert keep-max
  by `event_id`.
- `limit_snapshots(ts, provider, machine_id, window_kind, utilization_pct,
  resets_at, raw jsonb)`
- `sessions(session_id, provider, machine_id, project, slug, started_at,
  last_at, message_count, token totals)`
- `daily_rollups(day, provider, machine_id, model, project, token sums,
  cost_usd)` — refreshed on ingest.
- `pricing(provider, model, effective_date, per-MTok rates)` + overrides.
- `alert_rules`, `alert_events`, `api_tokens(hash, label, created_at,
  last_used)`.

## 6. API surface (OpenAPI published at /docs)

- `POST /api/v1/ingest/events|limits|bootstrap` — batch, idempotent; also
  usable by future custom sources.
- `GET /api/v1/summary/now` — gauges, burn rate, prediction ETA.
- `GET /api/v1/limits/current|history`, `GET /api/v1/blocks`.
- `GET /api/v1/usage?group_by=day|hour|provider|model|machine|project|session`
  with from/to and dimension filters; `provider` filters every endpoint.
- `GET /api/v1/sessions[/{id}]`, `/machines`, `/heatmap`, `/cost`,
  `/pricing`.
- `GET/PUT /api/v1/alerts`, `GET /api/v1/alerts/events`.
- `WS /api/v1/stream` — live events for the dashboard.
- Bearer-token auth on everything; ingest applies sanity validation
  (non-negative counts, token-math bounds).

## 7. Dashboard views (Vue 3 + TS + ECharts)

1. Now: limit gauges (5-hour, weekly, weekly-Opus) with reset countdowns and
   provenance badges; burn-rate sparkline with predicted exhaustion time;
   today's tokens by model; live per-machine feed over WebSocket.
2. Blocks: 5-hour block timeline with per-block tokens, peak burn, end
   utilization; utilization histogram.
3. Trends: daily stacked area (model/machine/project toggle); weekly bars vs
   cap; calendar heatmap and hour/weekday punch card; monthly equivalent-USD
   vs subscription price.
4. Breakdowns: project/machine/model/main-vs-subagent; cache efficiency and
   USD saved; web search/fetch counts.
5. Sessions: sortable table with per-request timeline drill-down.
6. Machines: fleet health, last seen, staleness, version drift.
7. Alerts and Settings: rule configuration, history, pricing overrides.

## 8. Alerting

Rules evaluated on ingest and on a timer: 5-hour block at 80/95 percent
(official), weekly at 80 percent, predicted block exhaustion before reset,
burn-rate anomaly (over 2x trailing median), collector stale over 30
minutes, unknown model. Channels: ntfy (primary), Telegram, SMTP; per-rule
routing, cooldowns, quiet hours.

## 9. Error handling principles

- Collector is crash-safe: byte offsets and upload queue persist in SQLite;
  every upload is idempotent; retries use exponential backoff.
- The OAuth limits source degrades to local estimates without failing the
  collector.
- Ingest rejects malformed batches with structured errors; partial batch
  acceptance is not allowed (all-or-nothing per batch).
- Unknown models are ingested with `cost_usd = NULL` and raise an alert;
  cost is recomputed when pricing arrives.

## 10. Testing

- Unit: JSONL parser fixtures (duplicate requestIds, streaming placeholders,
  5m/1h cache splits), dedup keep-max, pricing math per strategy, block
  boundary cases, alert rule evaluation, FakeProvider round trips.
- Integration: collector-to-server round trip against fixture transcripts;
  fake OAuth endpoint; API contract tests via FastAPI TestClient.
- Frontend: vitest for logic, ESLint/Prettier gates.
- Quality gates: pytest 100 percent pass, coverage 80 line / 70 branch,
  ruff zero warnings, mypy strict, trivy no HIGH/CRITICAL, shellcheck clean.
