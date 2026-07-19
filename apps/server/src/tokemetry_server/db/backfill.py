"""V1-to-v2 usage-event backfill and count/sum verification (FR-EVENT-023).

Migration Phase 2's highest-risk step: copy every historical ``usage_events``
row into ``usage_events_v2`` without loss, then prove equality before the
compatibility view replaces the physical table (subtask 62.10).

Backfill mapping: each v1 row becomes a v2 row with ``event_kind='attempt'``,
``finality='final'``, ``sequence=0``, the v1 ``model`` as ``native_model``,
requested/routed model null, ``source_id`` null (attributed by Task 63), the
five v1 token counters copied (``reasoning_tokens=0``), and the single v1 ``ts``
mapped onto both ``ts_started`` and ``ts_completed`` (v1 has one timestamp).
V1-only columns that have no v2 home (``git_branch``, ``client_version``,
``entrypoint``, ``is_sidechain``, ``session_kind``, ``speed``, ``source``, and
``cost_usd``) are preserved in ``extra`` under the :data:`V1_NAMESPACE` key so
the v1 compatibility view can reproduce them exactly. Backfilled rows carry a
:data:`BACKFILL_MARKER` so the migration downgrade can find and drop them.

The copy is chunked and keyset-paginated (resumable and idempotent via
``ON CONFLICT DO NOTHING``) so both SQLite and Postgres survive production-scale
tables, and it never mutates source rows. The verification aggregates both
tables in Python -- dialect-agnostic, no date/JSON SQL functions -- grouping by
day, provider, and machine and comparing counts, all five token sums, and cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tokemetry_server.db import models

#: Registry name of the derived source for legacy Claude Code collector traffic
#: (task 63.6); its identity is ``(collector, this name, machine id)``.
COLLECTOR_SOURCE_NAME = "claude-code-collector"

#: Marker set in a backfilled row's ``extra`` so the downgrade can find it.
BACKFILL_MARKER = "_backfill"
#: ``extra`` namespace holding v1-only columns with no dedicated v2 column.
V1_NAMESPACE = "_v1"
#: Rows copied/scanned per chunk; large enough to be fast, small enough to bound
#: transaction and memory footprint on production-scale tables.
DEFAULT_CHUNK_SIZE = 10_000

#: The five v1 token columns whose sums the verification compares.
_TOKEN_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
)


def _insert_ignore(dialect_name: str, table: Any, rows: list[dict[str, Any]]) -> Any:
    """Build an idempotent insert that skips rows already present."""
    stmt: Any
    if dialect_name == "postgresql":
        stmt = pg_insert(table).values(rows)
    elif dialect_name == "sqlite":
        stmt = sqlite_insert(table).values(rows)
    else:
        raise ValueError(f"unsupported dialect for backfill: {dialect_name}")
    return stmt.on_conflict_do_nothing(index_elements=["provider", "event_id"])


def _after(col_a: Any, col_b: Any, key: tuple[str, str]) -> Any:
    """Keyset predicate: the ``(col_a, col_b)`` pair strictly greater than ``key``."""
    return sa.tuple_(col_a, col_b) > sa.tuple_(sa.literal(key[0]), sa.literal(key[1]))


def _v2_row(row: sa.RowMapping) -> dict[str, Any]:
    """Map one v1 ``usage_events`` row onto a ``usage_events_v2`` row dict."""
    extra = dict(row["extra"] or {})
    cost = row["cost_usd"]
    extra[V1_NAMESPACE] = {
        "git_branch": row["git_branch"],
        "client_version": row["client_version"],
        "entrypoint": row["entrypoint"],
        "is_sidechain": row["is_sidechain"],
        "session_kind": row["session_kind"],
        "speed": row["speed"],
        "source": row["source"],
        "cost_usd": str(cost) if cost is not None else None,
    }
    extra[BACKFILL_MARKER] = True
    return {
        "provider": row["provider"],
        "event_id": row["event_id"],
        "schema_version": 2,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 0,
        "logical_request_id": None,
        "attempt_id": None,
        "provider_request_id": None,
        "provider_response_id": None,
        "requested_model": None,
        "routed_model": None,
        "native_model": row["model"],
        "ts_started": row["ts"],
        "ts_first_token": None,
        "ts_completed": row["ts"],
        "machine": row["machine"],
        "project": row["project"],
        "session_id": row["session_id"],
        "agent_id": None,
        "environment": None,
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_write_short_tokens": row["cache_write_short_tokens"],
        "cache_write_long_tokens": row["cache_write_long_tokens"],
        "reasoning_tokens": 0,
        "success": True,
        "outcome": None,
        "http_status": None,
        "stop_reason": None,
        "service_tier": row["service_tier"],
        "streaming": None,
        "latency_ms": None,
        "time_to_first_token_ms": None,
        "tool_call_count": 0,
        "tool_histogram": None,
        "provenance": row["provenance"],
        "cost_usd": cost,
        "source_id": None,
        "routing": None,
        "dimensions": {},
        "extra": extra,
        "trace_id": None,
        "span_id": None,
        "parent_span_id": None,
    }


def backfill_usage_events_v2(
    connection: Connection, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> int:
    """Copy every ``usage_events`` row into ``usage_events_v2``; return the count.

    Keyset-paginated by ``(provider, event_id)`` and idempotent (``ON CONFLICT
    DO NOTHING``), so an interrupted run resumes cleanly on re-invocation and
    source rows are never touched.
    """
    v1 = models.UsageEvent.__table__
    v2 = models.UsageEventV2.__table__
    processed = 0
    last: tuple[str, str] | None = None
    while True:
        stmt = sa.select(v1).order_by(v1.c.provider, v1.c.event_id).limit(chunk_size)
        if last is not None:
            stmt = stmt.where(_after(v1.c.provider, v1.c.event_id, last))
        rows = connection.execute(stmt).mappings().all()
        if not rows:
            break
        connection.execute(
            _insert_ignore(connection.dialect.name, v2, [_v2_row(row) for row in rows])
        )
        processed += len(rows)
        last = (rows[-1]["provider"], rows[-1]["event_id"])
        if len(rows) < chunk_size:
            break
    return processed


def remove_backfilled_rows(
    connection: Connection, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> int:
    """Delete only the rows this backfill created; return the count removed.

    Identifies backfilled rows by the :data:`BACKFILL_MARKER` in ``extra`` so a
    downgrade leaves any natively-ingested v2 rows untouched.
    """
    v2 = models.UsageEventV2.__table__
    removed = 0
    last: tuple[str, str] | None = None
    while True:
        stmt = (
            sa.select(v2.c.provider, v2.c.event_id, v2.c.extra)
            .order_by(v2.c.provider, v2.c.event_id)
            .limit(chunk_size)
        )
        if last is not None:
            stmt = stmt.where(_after(v2.c.provider, v2.c.event_id, last))
        rows = connection.execute(stmt).mappings().all()
        if not rows:
            break
        last = (rows[-1]["provider"], rows[-1]["event_id"])
        marked = [
            (row["provider"], row["event_id"])
            for row in rows
            if isinstance(row["extra"], dict) and row["extra"].get(BACKFILL_MARKER)
        ]
        for provider, event_id in marked:
            connection.execute(
                sa.delete(models.UsageEventV2).where(
                    models.UsageEventV2.provider == provider,
                    models.UsageEventV2.event_id == event_id,
                )
            )
        removed += len(marked)
        if len(rows) < chunk_size:
            break
    return removed


def populate_transitional_cost(
    connection: Connection, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> int:
    """Fill the transitional ``cost_usd`` column from ``extra['_v1']['cost_usd']``.

    Backfilled rows written before the column existed (migration 0008) keep the
    v1 cost only in ``extra``; migration 0009 calls this to copy it into the new
    column so the v1 compatibility view can expose cost. Returns rows updated.
    """
    v2 = models.UsageEventV2.__table__
    updated = 0
    last: tuple[str, str] | None = None
    while True:
        stmt = (
            sa.select(v2.c.provider, v2.c.event_id, v2.c.extra)
            .order_by(v2.c.provider, v2.c.event_id)
            .limit(chunk_size)
        )
        if last is not None:
            stmt = stmt.where(_after(v2.c.provider, v2.c.event_id, last))
        rows = connection.execute(stmt).mappings().all()
        if not rows:
            break
        last = (rows[-1]["provider"], rows[-1]["event_id"])
        for row in rows:
            extra = row["extra"] if isinstance(row["extra"], dict) else {}
            stored = extra.get(V1_NAMESPACE, {}).get("cost_usd")
            if stored is None:
                continue
            connection.execute(
                sa.update(models.UsageEventV2)
                .where(
                    models.UsageEventV2.provider == row["provider"],
                    models.UsageEventV2.event_id == row["event_id"],
                )
                .values(cost_usd=Decimal(stored))
            )
            updated += 1
        if len(rows) < chunk_size:
            break
    return updated


def attribute_backfilled_sources(connection: Connection) -> int:
    """Attribute source-less v1 rows to a derived collector source per machine.

    Historical rows created by the v1-to-v2 backfill (task 62.8) carry no
    ``source_id``. For each distinct machine, this resolves or creates a
    ``(collector, claude-code-collector, machine)`` source -- version pulled from
    the machines table, machine linked (FR-SOURCE-008) -- and stamps its id onto
    that machine's attempt rows. Returns the number of rows attributed.
    """
    event = models.UsageEventV2
    machine_ids = (
        connection.execute(
            sa.select(event.machine)
            .where(
                event.source_id.is_(None),
                event.machine.isnot(None),
                event.event_kind == "attempt",
            )
            .distinct()
        )
        .scalars()
        .all()
    )

    now = datetime.now(UTC)
    attributed = 0
    for machine in machine_ids:
        source_id = connection.execute(
            sa.select(models.Source.id).where(
                models.Source.type == "collector",
                models.Source.name == COLLECTOR_SOURCE_NAME,
                models.Source.instance_id == machine,
            )
        ).scalar()
        if source_id is None:
            version = connection.execute(
                sa.select(models.Machine.collector_version).where(
                    models.Machine.id == machine
                )
            ).scalar()
            inserted = connection.execute(
                sa.insert(models.Source).values(
                    type="collector",
                    name=COLLECTOR_SOURCE_NAME,
                    version=version,
                    instance_id=machine,
                    machine=machine,
                    token_label=None,
                    billing_mode="api_billed",
                    first_seen=now,
                    last_seen=now,
                    revoked=False,
                    recent_error_count=0,
                )
            ).inserted_primary_key
            assert inserted is not None
            source_id = inserted[0]
        result = connection.execute(
            sa.update(event)
            .where(
                event.machine == machine,
                event.source_id.is_(None),
                event.event_kind == "attempt",
            )
            .values(source_id=source_id)
        )
        attributed += result.rowcount or 0
    return attributed


def remove_collector_source_attribution(connection: Connection) -> int:
    """Reverse :func:`attribute_backfilled_sources`; return rows unlinked.

    Nulls ``source_id`` on rows pointing at derived collector sources and deletes
    those sources, so the migration downgrade leaves no dangling references.
    """
    ids = (
        connection.execute(
            sa.select(models.Source.id).where(
                models.Source.type == "collector",
                models.Source.name == COLLECTOR_SOURCE_NAME,
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0
    result = connection.execute(
        sa.update(models.UsageEventV2)
        .where(models.UsageEventV2.source_id.in_(ids))
        .values(source_id=None)
    )
    connection.execute(sa.delete(models.Source).where(models.Source.id.in_(ids)))
    return result.rowcount or 0


@dataclass(frozen=True)
class BackfillReport:
    """The machine-readable result of the count/sum verification."""

    groups_checked: int
    mismatches: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        """Whether every day/provider/machine group matched exactly."""
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the report."""
        return {
            "ok": self.ok,
            "groups_checked": self.groups_checked,
            "mismatches": list(self.mismatches),
        }


def _empty_totals() -> dict[str, Any]:
    """Zeroed accumulator for one group."""
    totals: dict[str, Any] = {"count": 0, "cost": Decimal(0)}
    for column in _TOKEN_COLUMNS:
        totals[column] = 0
    return totals


def _accumulate(
    aggregate: dict[tuple[date, str, str], dict[str, Any]],
    key: tuple[date, str, str],
    row: sa.RowMapping,
    cost: Decimal,
) -> None:
    """Add one row's counters into its group accumulator."""
    totals = aggregate.setdefault(key, _empty_totals())
    totals["count"] += 1
    totals["cost"] += cost
    for column in _TOKEN_COLUMNS:
        totals[column] += row[column] or 0


def _aggregate_v1(
    connection: Connection, chunk_size: int
) -> dict[tuple[date, str, str], dict[str, Any]]:
    """Aggregate ``usage_events`` by (day, provider, machine)."""
    table = models.UsageEvent.__table__
    aggregate: dict[tuple[date, str, str], dict[str, Any]] = {}
    last: tuple[str, str] | None = None
    while True:
        stmt = sa.select(table).order_by(table.c.provider, table.c.event_id).limit(chunk_size)
        if last is not None:
            stmt = stmt.where(_after(table.c.provider, table.c.event_id, last))
        rows = connection.execute(stmt).mappings().all()
        if not rows:
            break
        for row in rows:
            key = (row["ts"].date(), row["provider"], row["machine"] or "")
            cost = row["cost_usd"] if row["cost_usd"] is not None else Decimal(0)
            _accumulate(aggregate, key, row, cost)
        last = (rows[-1]["provider"], rows[-1]["event_id"])
        if len(rows) < chunk_size:
            break
    return aggregate


def _aggregate_v2(
    connection: Connection, chunk_size: int
) -> dict[tuple[date, str, str], dict[str, Any]]:
    """Aggregate backfilled ``usage_events_v2`` rows by (day, provider, machine)."""
    table = models.UsageEventV2.__table__
    aggregate: dict[tuple[date, str, str], dict[str, Any]] = {}
    last: tuple[str, str] | None = None
    while True:
        stmt = sa.select(table).order_by(table.c.provider, table.c.event_id).limit(chunk_size)
        if last is not None:
            stmt = stmt.where(_after(table.c.provider, table.c.event_id, last))
        rows = connection.execute(stmt).mappings().all()
        if not rows:
            break
        for row in rows:
            extra = row["extra"] if isinstance(row["extra"], dict) else {}
            if not extra.get(BACKFILL_MARKER):
                continue
            stored = extra.get(V1_NAMESPACE, {}).get("cost_usd")
            cost = Decimal(stored) if stored is not None else Decimal(0)
            key = (row["ts_started"].date(), row["provider"], row["machine"] or "")
            _accumulate(aggregate, key, row, cost)
        last = (rows[-1]["provider"], rows[-1]["event_id"])
        if len(rows) < chunk_size:
            break
    return aggregate


def verify_backfill(
    connection: Connection, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> BackfillReport:
    """Compare v1 and backfilled-v2 counts and sums per day/provider/machine."""
    v1_aggregate = _aggregate_v1(connection, chunk_size)
    v2_aggregate = _aggregate_v2(connection, chunk_size)

    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(v1_aggregate) | set(v2_aggregate)):
        v1_totals = v1_aggregate.get(key)
        v2_totals = v2_aggregate.get(key)
        if v1_totals != v2_totals:
            day, provider, machine = key
            mismatches.append(
                {
                    "group": {
                        "day": day.isoformat(),
                        "provider": provider,
                        "machine": machine,
                    },
                    "v1": _serialize_totals(v1_totals),
                    "v2": _serialize_totals(v2_totals),
                }
            )
    return BackfillReport(
        groups_checked=len(set(v1_aggregate) | set(v2_aggregate)),
        mismatches=tuple(mismatches),
    )


def _serialize_totals(totals: dict[str, Any] | None) -> dict[str, Any] | None:
    """JSON-safe view of a group's totals (Decimal cost to string)."""
    if totals is None:
        return None
    return {**totals, "cost": str(totals["cost"])}
