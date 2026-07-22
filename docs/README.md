# tokemetry documentation

Self-hosted, multi-machine AI token usage tracking. Start with
[deployment](#deployment) to stand up a server and collectors, then use the
[operations](#operations) runbooks to run it and the [API](#api) reference to
build on it.

## Deployment

- [Server](deployment/server.md) — Docker Compose on a VPS behind WireGuard, or
  the native systemd unit.
- [Backup and restore](deployment/backup-restore.md) — nightly dumps, automated
  restore verification, and the manual restore drill.
- [Grafana](deployment/grafana.md) — point Grafana at the database.
- [API clients](deployment/api-clients.md) — provision scoped bearer tokens for
  other apps.

## Collectors

Run one per machine that uses Claude Code.

- [Overview](deployment/collector.md) — install, configure, and run as a service
  (all platforms), including the one-command installer.
- Per platform: [Windows](deployment/collector-windows.md),
  [Linux](deployment/collector-linux.md), [macOS](deployment/collector-macos.md).
- [Collector internals](collector/overview.md) — what it reads and uploads.

## Operations

- [Runbooks](operations/README.md) — operating a production deployment, with
  go/no-go criteria and failure-mode signals.
- [Migration](operations/migration.md), [Rollback](operations/rollback.md),
  [Upgrade](operations/upgrade.md),
  [Token rotation](operations/token-rotation.md).
- [Alerting](alerting.md) — ntfy, Telegram, and SMTP channels and rules.

## API

- Ingest: [v1](api/ingest.md), [v2 (provider-neutral)](api/ingest-v2.md).
- Query: [v1](api/query.md), [v2](api/query-v2.md).
- [Sources](api/sources.md), [registries](api/registries.md),
  [gateway limits](api/gateway-limits.md).

## Integrations

- [AI provider proxy](integrations/ai-provider-proxy.md).
- [OpenTelemetry](integrations/opentelemetry.md).

## Architecture

- [Provider-neutral baseline](architecture/provider-neutral-baseline.md),
  [core package](architecture/core-package.md),
  [database](architecture/database.md).
- Event and pricing model: [usage event v2](architecture/usage-event-v2.md),
  [event model v2](architecture/event-model-v2.md),
  [pricing v2](architecture/pricing-v2.md), [limits v2](architecture/limits-v2.md).
- [Data retention](architecture/retention.md),
  [stored fields](architecture/stored-fields.md),
  [session analytics](architecture/session-analytics.md),
  [source health](architecture/source-health.md),
  [performance](architecture/performance.md),
  [OpenTelemetry mapping](architecture/otel-mapping.md).
