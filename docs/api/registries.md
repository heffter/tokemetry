# Registry API (v2)

Read-only endpoints exposing the provider and model registries. All endpoints
require a bearer token (see [ingest.md](ingest.md)). The full OpenAPI schema is
at `/docs` and `/openapi.json`.

## Providers

- `GET /api/v2/providers` -- every provider in the registry, ordered by id.

Each row carries the full registry metadata (FR-PROVIDER-010): `id`,
`display_name`, `aliases`, `pricing_strategy`, `limit_semantics`,
`supported_dimensions`, and `registered`. A `registered=false` row is a
provider observed during ingest that has no built-in adapter yet (an unknown
provider accepted under the default policy).

## Models

- `GET /api/v2/models` -- model registry rows, ordered by `(provider,
  native_model_id)`.
- Filters: `provider=<id>` and `lifecycle=<active|deprecated|retired|unknown>`
  (an invalid lifecycle is rejected with `422`).

Each row returns `provider`, `native_model_id`, `lifecycle`, `capabilities`,
`first_seen`, `last_seen`, and `aliases` -- the alternate spellings that
normalize to the native id, so both the native and normalized forms are visible
(FR-MODEL-010). Models observed during ingest but not yet catalogued appear
with `lifecycle: "unknown"`.
