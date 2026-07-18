# Grafana

The schema is a set of plain, timestamped tables (see
[database](../architecture/database.md)), so Grafana can query Postgres
directly alongside the built-in dashboard.

## Add the datasource

1. In Grafana, add a **PostgreSQL** datasource pointing at the same database
   the server uses (host `db` on the compose network, or the VPS address and
   the mapped Postgres port if you expose it on WireGuard).
2. Use a read-only Postgres role for Grafana:

   ```sql
   CREATE ROLE grafana LOGIN PASSWORD 'change-me';
   GRANT CONNECT ON DATABASE tokemetry TO grafana;
   GRANT USAGE ON SCHEMA public TO grafana;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana;
   ```

## The `usage_events` view (v1 compatibility)

As of the provider-neutral v2 migration, `usage_events` is a **read-only view**
projecting the active attempt rows of `usage_events_v2` back to the exact v1
column shape (`ts`, `model`, `cost_usd`, `is_sidechain`, the five token
counters, ...), per decision D-001. Existing Grafana queries against
`usage_events` keep working unchanged and return identical results; the guarantee
is that the view's column list and semantics stay v1-compatible. The physical v1
rows are retained in `usage_events_v1_archive` until the retention policy (Task
70) handles them. New reasoning tokens and other v2-only fields are available on
`usage_events_v2` directly.

## Useful queries

Daily tokens by model:

```sql
SELECT day AS time, model, sum(total_tokens) AS tokens
FROM daily_rollups
GROUP BY day, model
ORDER BY day;
```

Daily cost:

```sql
SELECT day AS time, sum(cost_usd) AS cost_usd
FROM daily_rollups
GROUP BY day
ORDER BY day;
```

Latest limit utilization:

```sql
SELECT DISTINCT ON (window_kind) ts AS time, window_kind, utilization_pct
FROM limit_snapshots
ORDER BY window_kind, ts DESC;
```

Per-machine event volume:

```sql
SELECT machine, count(*) AS events, sum(input_tokens + output_tokens) AS tokens
FROM usage_events
GROUP BY machine;
```

The custom dashboard and Grafana can coexist; Grafana is optional and does
not replace the limit gauges/block timeline, which are simpler in the
built-in UI.
