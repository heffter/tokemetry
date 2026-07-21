# Operations runbooks

Operating a production tokemetry deployment. Each runbook is a controlled
procedure with go/no-go criteria.

- [Migration](migration.md) -- apply an Alembic schema migration (AC-013).
- [Rollback](rollback.md) -- revert a bad migration or release (AC-018).
- [Token rotation](token-rotation.md) -- rotate scoped bearer tokens with no
  downtime (FR-SEC-009).
- [Upgrade](upgrade.md) -- upgrade server/collector/proxy and the schema-version
  compatibility rules (NFR-REL-005).

Related: [backup and restore](../deployment/backup-restore.md),
[server deployment](../deployment/server.md),
[retention](../architecture/retention.md).

## Operational status at a glance

The dashboard **Settings** view surfaces content-free operational status so an
operator sees health without shell access:

- **Retention** -- per-category last run, rows deleted, backlog, and oldest
  retained (`GET /api/v2/admin/retention/status`).
- **Schema head** -- the running Alembic revision (from the migration verifier).
- **Backup age** -- age of the most recent verified dump.

## Failure modes and metrics

Watch these signals; each has an alert kind or a status surface:

| Signal | Where it shows | Runbook / action |
|---|---|---|
| Source stopped ingesting | `stale_source` alert; Sources view | Check the client; [token rotation](token-rotation.md) if auth |
| Schema version drift | `schema_drift` alert; data-quality events | [Upgrade](upgrade.md) -- a client emits an unsupported version |
| Retention worker not running | Retention status `last_run` stale | Check `TOKEMETRY_RETENTION_WORKER_ENABLED` and server logs |
| Rollup mismatch blocking deletion | `retention_rollup_mismatch` data-quality event | Rebuild rollups for the day; the worker retries |
| Backup stale or failing | Backup age; `verify-restore.sh` non-zero | [backup-restore](../deployment/backup-restore.md) |
| Rate-limit saturation | `429` responses with `Retry-After` | Tune the rate-limit capacities ([server](../deployment/server.md)) |
| Migration failed | Startup logs; `restore_verify` not-at-head | [Rollback](rollback.md) |

Key operational metrics to track over time: ingest throughput and reject rate,
query latency, retention rows-deleted per run and backlog, backup age, and open
data-quality event counts.
