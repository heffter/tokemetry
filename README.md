# tokemetry

Self-hosted, multi-machine AI token usage tracking: per-machine collectors, a
central API-first server, and a detailed web dashboard.

tokemetry ingests usage data from AI coding tools (Claude Code first, other
providers via a plugin architecture), stores it durably in Postgres on your own
server, and presents it as live limit gauges, burn-rate predictions, and
historical analytics. Everything the dashboard shows is available through a
documented REST API, so other applications can consume the same data.

## Why

- Claude Code deletes local transcripts after about 30 days; tokemetry
  preserves usage history indefinitely.
- No existing tool aggregates usage across multiple machines; tokemetry is
  built for a fleet (Windows, Linux, macOS).
- Subscription users (Pro/Max) need limit-centric views: 5-hour block and
  weekly cap utilization, time-to-reset, and predicted exhaustion, with
  equivalent API cost as a value metric.

## Architecture

```
Machine A/B/C (Win/Linux/macOS)          Server (VPN-only)
+---------------------------+          +----------------------------------+
| collector (Python daemon) |  HTTPS   | FastAPI service                  |
|  - JSONL watcher/tailer   |--------->|  - ingest API (idempotent)       |
|  - stats-cache bootstrap  |          |  - Postgres                      |
|  - OAuth limits poller    |          |  - block/burn/cost engines       |
|  - SQLite offline queue   |          |  - alert engine (ntfy/TG/SMTP)   |
+---------------------------+          |  - REST API + WebSocket + SPA    |
                                       +----------------------------------+
```

## Repository layout

| Path | Purpose |
|---|---|
| `apps/server` | FastAPI service: ingest, query API, engines, alerting |
| `apps/collector` | Cross-platform usage collector daemon |
| `apps/dashboard` | Vue 3 + TypeScript + ECharts web dashboard |
| `apps/website` | Astro public website and lightweight docs |
| `packages/core` | Shared models, provider abstractions, pricing |
| `deploy` | Docker Compose, systemd/launchd/Scheduled Task units |
| `docs` | Architecture, API, and operations documentation |

## Development

Python 3.12+, managed with [uv](https://docs.astral.sh/uv/) as a workspace.

```
uv sync                 # install all workspace members + dev tools
uv run pytest           # run tests
uv run ruff check .     # lint
uv run mypy .           # strict type check
```

Quality gates: pytest (80% line / 70% branch coverage), ruff (zero warnings),
mypy strict, trivy (no HIGH/CRITICAL), shellcheck for shell scripts.

## License

MIT — see [LICENSE](LICENSE).
