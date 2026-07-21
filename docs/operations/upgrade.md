# Upgrade runbook

Upgrading the server, collector, and proxy, and the version-compatibility
rules that keep them interoperable (NFR-REL-005).

## Compatibility model

Ingest is negotiated by **`schema_version`**, not by matching build numbers.
The server accepts every schema version it knows; a client sends the highest
version it emits. This decouples the three components:

- The **server** is the schema authority. Upgrade it first. A newer server
  keeps accepting older clients' payloads (v1 stays wire-compatible for the
  whole program; v2 accepts `schema_version: 2`).
- **Collectors** and the **proxy** can lag the server safely -- they keep
  sending their current schema version until upgraded.
- Never run a client that emits a schema version **newer** than the deployed
  server. That is the one unsafe ordering; upgrade the server first.

| Component | Depends on | Safe to lag? | Rule |
|---|---|---|---|
| Server | -- | -- | Upgrade first; migrates schema on startup |
| Collector | Server schema | Yes | Emits its schema version; server accepts older |
| Proxy | Server schema | Yes | Same negotiation as the collector |

## Upgrade order

1. **Server**: back up ([backup-restore](../deployment/backup-restore.md)),
   then follow the [migration runbook](migration.md) (pull image, migrate on
   startup, verify). Roll back per the [rollback runbook](rollback.md) on
   no-go.
2. **Collector**: update the service on each machine
   ([collector docs](../deployment/collector.md)); it resumes with the same or
   a newer schema version. No server change needed.
3. **Proxy**: update the gateway; it continues ingesting under the negotiated
   schema version.

## Verification

- Server: `restore_verify` reports at-head; the dashboard renders.
- Each client: its source keeps ingesting (`last_used` advances; source health
  is green in the Sources view).
- No `schema_drift` data-quality events or `schema_drift` alerts fire after the
  upgrade (a client emitting an unsupported version would surface here).

## Rollback

Server rollback follows the [rollback runbook](rollback.md). Clients roll back
by redeploying their prior build; because they only ever emit a schema version
the server already understands, a client rollback needs no server change.
