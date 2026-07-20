# tokemetry ingest clients

Generated clients for the tokemetry v2 ingest API, for proxy exporters and
importers (Task 65). Both clients are thin, hand-written wrappers over
machine-generated wire models; the wrappers add auth, batching, and a resilient
submit policy (D-012), while the wire models stay in lockstep with the server.

## Layout

| Path | What it is |
| --- | --- |
| `openapi.json` | The OpenAPI spec, generated from the FastAPI app. **Single source of truth.** |
| `generate_openapi.py` | Emits `openapi.json` (augmented with the `UsageEventV2` / `LimitSnapshotV2` ingest schemas, which the endpoints validate manually so FastAPI omits them). |
| `typescript/` | `@tokemetry/client`: `src/generated.ts` (generated types) + `src/client.ts` (hand-written `IngestClient`). |
| `python/` | `tokemetry-client`: `src/tokemetry_client/models.py` (generated) + `client.py` (hand-written `IngestClient` and `AsyncIngestClient`). |
| `codegen.py` | Regenerates all three artifacts from the spec; `--check` fails on drift. |

## Submit policy (both clients)

Identical semantics across the TypeScript and Python wrappers:

- **Batching** by event count (default 100) and serialized size (default 256 KiB).
- **Retry** on 429/5xx with full-jitter exponential backoff (base 200 ms, up to 5 attempts).
- **Pause** on 401 (`IngestAuthError`) without retry -- a bad token should not be hammered.
- **Poison isolation** on 400/422: a rejected multi-event batch is bisected until the
  offending event is isolated (surfaced in `poison_events`), so one malformed event
  never blocks the rest.

The Python package ships both a synchronous `IngestClient` (httpx.Client) and an
asynchronous `AsyncIngestClient` (httpx.AsyncClient) with the same surface.

## Regenerating after a schema change

```sh
# From the repo root, inside the uv-managed environment:
uv run python packages/clients/codegen.py
```

Then commit `openapi.json`, `python/src/tokemetry_client/models.py`, and
`typescript/src/generated.ts` together.

## Drift check (CI)

```sh
uv run python packages/clients/codegen.py --check
```

Regenerates every artifact and runs `git diff --exit-code`; a non-zero exit
means a committed artifact is stale versus the current server schema. Wire this
into CI so a schema change without regenerated clients fails the build.

## Generated code and quality gates

`python/src/tokemetry_client/models.py` is machine-generated and excluded from
the repo-wide `ruff` and `mypy` gates (see `pyproject.toml`); it is regenerated,
not hand-maintained. The hand-written wrappers and their tests are fully gated
(mypy strict, ruff clean).
