"""Query and rollup performance benchmark harness (Task 66.8).

A reusable synthetic-dataset generator and timing harness for the v2 query and
rollup NFRs (NFR-PERF-003, FR-ROLLUP-012). The functional smoke test seeds a
small dataset so the harness stays correct in CI; the reference-hardware numbers
(90 days, 1M attempts) are produced by running the harness against Postgres and
recorded in ``docs/architecture/performance.md``. Task 70 reuses this harness for
the sustained-ingest gate.
"""
