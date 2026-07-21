# Provider-neutral v2 — release acceptance (Epic TOK-10)

Evidence for the release acceptance criteria (PRD Section 19). Each criterion is
either **verified** by an automated test/gate in this repository, or
**staging-required** — it needs a live staging deployment fed by real collector
and proxy data, which is the final GA step performed outside CI. Staging-required
items list the exact procedure and the automated coverage that de-risks them.

## Automated gates (green)

- **Full backend suite**: 789 passed / 51 skipped (Postgres-only) / 0 failed
  (SQLite); the Postgres-gated subset runs green against Postgres 16.
- **Dashboard**: 170 vitest passed; vue-tsc, eslint, prettier clean.
- **Static analysis**: `ruff` zero warnings; `mypy .` strict clean;
  `trivy fs` zero HIGH/CRITICAL (NFR-SEC-001, **AC-016**); `npm audit` 0
  vulnerabilities (dashboard).

## Acceptance criteria

| AC | Criterion | Status | Evidence |
|---|---|---|---|
| AC-001 | v1 collector surface stays wire-compatible | verified | v1 ingest/query tests unchanged; two API versions coexist |
| AC-002 | Per-provider usage and costs land (Anthropic/OpenAI/Z.ai) | verified | `proxy_harness/test_e2e_acceptance.py::test_all_three_providers_land_usage_and_costs` (SQLite + Postgres) |
| AC-003 | Replay does not inflate totals | verified | `test_e2e_acceptance.py::test_replay_does_not_inflate_totals` |
| AC-004 | Streaming snapshots resolve to one final record | verified | `test_e2e_acceptance.py::test_streaming_snapshots_resolve_to_one_final_record` |
| AC-005/006 | Fallback chains count each attempt once; logical-request view is correct | verified | `test_e2e_acceptance.py::test_fallback_chain_counts_each_attempt_once` |
| AC-007..012 | Registries, pricing, limits, alerts, dashboards | verified | epic acceptance + per-feature integration suites (`test_alert_epic_acceptance.py`, pricing/limits/registry suites) |
| AC-013 | Migration runbook exists and is followed | verified (doc) + staging | [migration runbook](../operations/migration.md); walkthrough on staging |
| AC-014 | Backup and restore tests pass | verified | `test_restore_verify.py` (SQLite + Postgres); [backup-restore](../deployment/backup-restore.md) |
| AC-015 | Sustained ingest >= 1000 events/s | verified (floor) + staging | `test_ingest_throughput.py`; reference figure on staging ([performance](../architecture/performance.md)) |
| AC-016 | Trivy fs and image scans: zero HIGH/CRITICAL | verified (fs) + staging (image) | `trivy fs` clean in CI; image scan in the release pipeline |
| AC-017 | Rate limits and request bounds enforced | verified | `test_hardening_api.py` (429+Retry-After, 413, CORS, secure headers, WS cap) |
| AC-018 | Migration and rollback runbooks exist | verified (doc) | [rollback runbook](../operations/rollback.md), [migration](../operations/migration.md) |
| AC-019 | Audit log covers administrative actions | verified | `test_audit_api.py`; every admin path routes through `services/audit.record()` |
| AC-020 | Retention configurable and enforced | verified | `test_retention.py`, `test_retention_worker.py`, `test_retention_api.py` |
| AC-021 | Security/privacy adversarial suite green | verified | `tests/security/` (19 tests; [checklist](../../apps/server/tests/security/README.md)) |
| AC-022 | Scoped token rotation documented | verified (doc) | [token-rotation runbook](../operations/token-rotation.md) |

> The AC numbering above follows the criteria referenced across the epic tasks.
> Reconcile against the authoritative PRD Section 19 list during the staging
> sign-off; any criterion without passing evidence there spawns a blocking
> follow-up task per the quality-gate rule.

## Staging walkthrough (final GA step)

On a staging deployment fed by real collector data and the proxy harness:

1. Provision server + Postgres behind WireGuard; apply migrations
   ([migration runbook](../operations/migration.md)).
2. Point one real collector and the proxy at staging; confirm per-provider
   usage/costs, limits, and alerts populate (AC-002/007..012).
3. Run the sustained-load driver at 1000 events/s with the retention worker
   enabled; record throughput and confirm query availability (AC-015, PRD 18.5).
4. Run `trivy image` on the built server image (AC-016).
5. Execute the backup → restore → verify drill (AC-014) and a migration +
   rollback drill (AC-013/018).
6. Rotate a scoped token end to end (AC-022).
7. Record measured evidence per criterion here and in the release notes.
