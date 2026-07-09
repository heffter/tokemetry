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
