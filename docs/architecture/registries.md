# Provider and model registries

The registries are the provider-neutral core of tokemetry v2 (Epic TOK-2): a
canonical list of providers and the models seen for each, plus the alias rules
that map vendor spellings onto them. They are **lookup data only** -- no usage
row has a foreign key into them (FR-MODEL-007), so registry edits never rewrite
history. Storage lives in `providers`, `models`, and `model_aliases`
([database.md](database.md)); the API is [registries.md](../api/registries.md).

## Provider descriptor

A provider is described by a frozen `ProviderDescriptor`
(`tokemetry_core.models`), the single definition shared by collector and
server:

| Field | Meaning |
|---|---|
| `id` | Lowercase, stripped stable identifier (FR-PROVIDER-002). |
| `display_name` | Human-facing name. |
| `aliases` | Alternate spellings, stored lowercased. |
| `pricing_strategy` | Name of the pricing strategy to apply. |
| `limit_semantics` | How this provider's limit windows behave. |
| `supported_dimensions` | Usage dimensions the provider reports. |

The three seed descriptors (`tokemetry_core.normalization`) are Anthropic,
OpenAI, and Z.ai (FR-PROVIDER-008). The `providers` table adds a `registered`
flag and timestamps; `seed_default_providers` inserts the seeds on missing ids
only, so UI edits survive a restart.

## Alias rules and versioning

Alias normalization is centralized in `tokemetry_core.normalization`
(FR-PROVIDER-003) so no other module hardcodes vendor spellings.
`normalize_provider(raw)` is pure, case-insensitive, and idempotent: a known
alias resolves to its canonical id, an unknown value passes through stripped
and lowercased. The alias table is derived from the seed descriptors, so
`z.ai`, `z-ai`, and `z_ai` all land as `zai`.

`PROVIDER_NORMALIZATION_VERSION` versions the provider rule set. Model aliases
are stored in `model_aliases`, unique on `(provider, alias)` and carrying a
`rule_version` (FR-MODEL-009) so mappings produced by an older ruleset can be
recomputed. Model aliases are kept separate from native ids (FR-MODEL-002); the
native id is always retained verbatim (FR-MODEL-001).

## Model lifecycle

Each `models` row is keyed by `(provider, native_model_id)` -- the same grain
events carry -- with a `lifecycle` that is one of (FR-MODEL-004):

- `active` -- a current, recognized model.
- `deprecated` -- still accepted, scheduled for removal.
- `retired` -- no longer offered.
- `unknown` -- observed in usage but not yet catalogued.

Lifecycle is an enum-as-string validated in the service layer, not a database
enum, so the schema stays portable across SQLite and Postgres. `capabilities`
is a free-form JSON map for display and validation (FR-MODEL-005).

## Unknown-entity policy

Ingest resolves every event's provider through the registry. A known provider
(seed or previously registered) passes through. An unknown provider is handled
by `registry_unknown_provider_policy` (FR-PROVIDER-005):

- `accept` (default) -- the event is stored and the provider is recorded with
  `registered=False`, plus an `unknown_provider` data-quality event.
- `reject` -- the batch is refused with `400`.

Every event's native model is observed: a known model's `last_seen` advances; an
unknown model is inserted with lifecycle `unknown` and an `unknown_model`
data-quality event (FR-MODEL-006). Data-quality recording is fire-and-forget
(SAVEPOINT-isolated) so it never fails otherwise-valid ingest. A database that
predates the registries is reconciled once by the startup backfill (see
[database.md](database.md)).

## Registering a new provider

Adding a provider is data and adapter work only -- never dashboard code
(FR-PROVIDER-007):

1. Add a `ProviderDescriptor` (id, display name, aliases, pricing strategy,
   limit semantics, supported dimensions) to the seed list in
   `tokemetry_core.normalization`, and register its adapters in the provider's
   `register(registry)` (pricing/usage/limits) as they are implemented.
2. Restart: `seed_default_providers` inserts the new descriptor; models are
   observed from ingest or filled by the backfill.
3. The provider and its models are immediately visible through
   `GET /api/v2/providers` and `GET /api/v2/models` with no frontend change.

## Requirement coverage

| Requirement | Status |
|---|---|
| FR-PROVIDER-001 canonical registry | Implemented (`providers`, `ProviderRegistryService`). |
| FR-PROVIDER-002 lowercase stable ids | Implemented (`ProviderDescriptor` id validator). |
| FR-PROVIDER-003 central alias normalization | Implemented (`normalize_provider`). |
| FR-PROVIDER-004 retained metadata | Implemented (descriptor + `providers`). |
| FR-PROVIDER-005 unknown providers marked unregistered | Implemented (policy + `resolve`). |
| FR-PROVIDER-006 provider-namespaced extensions | Partial: `UsageEvent.extra` carries provider-specific counters; full wire-schema namespacing is deferred to the event-schema epic (TOK-4). |
| FR-PROVIDER-007 no dashboard change to register | Implemented (seed data + adapters only). |
| FR-PROVIDER-008 seed Anthropic/OpenAI/Z.ai | Implemented (seed descriptors). |
| FR-PROVIDER-009 Claude Code ids compatible | Implemented (`anthropic` unchanged; v1 golden green). |
| FR-PROVIDER-010 metadata queryable via API | Implemented (`GET /api/v2/providers`). |
| FR-MODEL-001 retain native model id | Implemented (native id stored verbatim). |
| FR-MODEL-002 aliases separate from native ids | Implemented (`model_aliases`). |
| FR-MODEL-003 pricing by provider+native id+date+dimensions | Deferred to the pricing epic (TOK-5); current pricing keys on provider+model+date. |
| FR-MODEL-004 lifecycle states | Implemented (`models.lifecycle`). |
| FR-MODEL-005 capability metadata | Implemented (`models.capabilities`). |
| FR-MODEL-006 unknown models in data-quality reports | Implemented recording + `open_events`; the `/api/v2/data-quality` endpoint is deferred to TOK-9. |
| FR-MODEL-007 metadata updates never rewrite events | Implemented (no FK; observe/backfill never touch usage rows). |
| FR-MODEL-008 Claude/OpenAI-Codex/GLM families | Implemented (seed providers + three-provider test fixtures; backfill classifies Claude families active). |
| FR-MODEL-009 versioned alias rules | Implemented (`model_aliases.rule_version`, `PROVIDER_NORMALIZATION_VERSION`). |
| FR-MODEL-010 native and normalized fields in API | Implemented (`GET /api/v2/models` returns native id plus aliases). |
