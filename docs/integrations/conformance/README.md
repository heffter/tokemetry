# AI provider proxy conformance sets

Versioned golden request/response pairs that pin the tokemetry v2 ingest
contract. A proxy exporter vendors a version directory (e.g. `v2.0/`) and runs
its own snapshot tests against it, so both sides evolve against the same
artifact (companion FR-TOK-030). The tokemetry server replays the same set in
CI (`apps/server/tests/integration/proxy_harness/test_conformance_v2.py`).

## Layout

Each `v<major.minor>/` directory holds a single `conformance.json`:

- `conformance_version`, `schema_version`, `endpoint` -- what the set targets.
- `valid_batches[]` -- `{ name, provider, request, expected_response }`. The
  `request` is a full `POST /api/v2/ingest/events` body; `expected_response`
  lists the deterministic ingest counts (`accepted`, `updated`, `duplicate`,
  `rejected`, `corrected`). The non-deterministic `batch_id` / `request_id` are
  intentionally excluded.
- `invalid_batches[]` -- `{ name, reason, request, expected_status,
  expected_errors }`. Each expected error matches by batch `index`, error
  `code`, and a substring the `field_path` must contain. These lock the
  content-free policy (content in `extra`, `dimensions`, or `tool_histogram` is
  rejected -- FR-TOK-029, AC-011) and poison-index behavior (a single invalid
  event in a batch surfaces its index so a client's recursive splitting
  terminates -- FR-TOK-015).

## Compatibility promise (PP-010)

- The set is **versioned, never mutated in place**. Once `v2.0/conformance.json`
  is published, its golden outcomes do not change.
- A v2 schema change that alters any golden outcome must be introduced as a new
  version directory (e.g. `v2.1/`), with the previous version retained. The
  server's conformance test fails until the set is regenerated and versioned
  forward -- there is no silent contract drift.
- Additive, backward-compatible changes (a new optional field the server ignores
  when absent) do not require a new version; they may be reflected in the next
  version at the maintainers' discretion.
- A `major` bump (`v3.x`) signals a breaking wire change requiring a client
  regeneration; a `minor` bump signals a compatible refinement of the golden set.

## Regenerating

The set is generated from the harness fixtures and conformance cases:

```sh
uv run python -c "import json,sys; sys.path.insert(0,'apps/server/tests/integration'); \
from proxy_harness.conformance import build_conformance_document as b; \
open('docs/integrations/conformance/v2.0/conformance.json','w',newline='\n').write(json.dumps(b(),indent=2)+'\n')"
```

`test_golden_export_is_in_sync` asserts the committed file equals the generated
document, so a drift between the fixtures and the export fails CI.
