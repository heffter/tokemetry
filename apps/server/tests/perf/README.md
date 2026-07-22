# Performance tests

This directory holds the v2 query/rollup benchmark harness and the sustained
ingest-throughput test. Two of these assert wall-clock behaviour, which is only
meaningful on hardware that is not otherwise busy, so the policy below keeps the
default gate deterministic while still catching real regressions.

## Policy

- **Correctness always runs.** `test_benchmark_smoke.py` (harness/generator
  shape and counts) and the `accepted == total` check in
  `test_ingest_throughput.py` assert correctness, not wall-clock, and run in the
  default gate on any hardware.
- **Wall-clock assertions are opt-in.** `test_sustained_ingest_throughput` is
  marked `@pytest.mark.perf`. The default gate excludes `perf`
  (`addopts = -ra -m "not perf"` in the root `pyproject.toml`), so
  `uv run pytest` never gates on timing. A command-line `-m` overrides that
  default, so `uv run pytest -m perf` runs the perf tests explicitly.
- **The floor is not lowered; it is load-gated.** The 100 events/s floor is a
  10x margin below the >= 1000 events/s reference target. When the perf test
  runs, it first checks machine load (`throughput_guard.machine_load_reason`):
  if RAM is at/above 85% or ambient CPU is at/above 60%, it `pytest.skip`s with a
  clear reason instead of asserting timing. On an idle machine it enforces the
  floor, so an order-of-magnitude regression (e.g. 50 events/s) still fails.

## Why (2026-07-22)

`test_sustained_ingest_throughput` asserted `rate > 100` events/s and passed a
full-suite run, then measured 81, 87, and 89 events/s across three consecutive
runs on the same checkout -- no ingest-path change, only an unrelated uv workspace
edit. The machine was under heavy load: 89% RAM and 33 concurrent
node/python/uv processes from parallel agent sessions. The floor sat inside the
loaded-machine range, so the outcome depended on ambient load rather than the
code. Load-gating removes that non-determinism without hiding a real regression.

## Running

- Default gate (no timing): `uv run pytest -q`
- Perf tests explicitly: `uv run pytest -m perf`
  - On a loaded machine these skip with a clear reason; on an idle machine they
    enforce the floor.
- Reference figures (Postgres, full dataset) are produced with the harness and
  recorded in `docs/architecture/performance.md`.

## Guard internals

`throughput_guard.py` exposes:

- `MIN_RATE` -- the events/s floor (100.0).
- `MAX_MEM_PERCENT` / `MAX_CPU_PERCENT` -- load thresholds above which timing is
  skipped.
- `machine_load_reason()` -- a skip reason string, or `None` when timing is
  trustworthy.
- `throughput_regression(rate)` -- a failure message when `rate` is at/below the
  floor, or `None`.

`test_throughput_guard.py` unit-tests these with mocked load metrics, so the
regression-detection and skip-under-load behaviours are covered deterministically
in the default gate.
