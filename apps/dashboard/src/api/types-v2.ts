// Wire types for the provider-neutral v2 read API.
//
// These mirror the server response models in
// apps/server/src/tokemetry_server/api/v2/schemas.py (the Pydantic ``*Out``
// classes, all ``extra="forbid"``). Keep them in sync with that file: the
// server is the source of truth and its integration tests assert the shapes.
//
// Serialization conventions carried over from the v1 types (see ./types.ts):
//   - money (Pydantic ``Decimal``) is serialized as a JSON string to preserve
//     precision, so every ``*_usd`` / ``unit_price`` / ``utilization_pct``
//     field is ``string`` (or ``string | null`` when the server model is
//     ``Decimal | None``);
//   - datetimes and dates are ISO-8601 strings;
//   - ``dict[str, Any]`` becomes ``Record<string, unknown>``.

// --- Registries (providers, models) ---

/** One limit-window descriptor from the provider registry (WindowOut). */
export interface WindowV2 {
  kind: string;
  label: string;
  /** 'rolling' | 'calendar' | 'opaque'. */
  period_kind: string;
  period_seconds: number | null;
  sort_order: number;
}

/** Provider registry metadata (ProviderOut). */
export interface ProviderV2 {
  id: string;
  display_name: string;
  aliases: string[];
  pricing_strategy: string;
  limit_semantics: string;
  supported_dimensions: string[];
  /** Limit-window kinds/labels/period semantics (FR-LIMIT-012). */
  windows: WindowV2[];
  /** False for observed-but-unregistered providers. */
  registered: boolean;
}

/** Model lifecycle values accepted as a filter (FR-MODEL-004). */
export type ModelLifecycle = 'active' | 'deprecated' | 'retired' | 'unknown';

/** Model registry metadata with alias spellings (ModelOut). */
export interface ModelV2 {
  provider: string;
  native_model_id: string;
  lifecycle: string;
  capabilities: Record<string, unknown>;
  first_seen: string | null;
  last_seen: string | null;
  aliases: string[];
}

// --- Shared envelope pieces ---

/** A data-quality caveat attached to a usage/cost response (QueryWarningOut). */
export interface QueryWarning {
  kind: string;
  detail: string;
  count: number;
}

// --- Usage ---

/** One grouped usage aggregate: six token counters plus attempts (UsageRowOut). */
export interface UsageRowV2 {
  key: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  attempt_count: number;
}

/** Grouped final-attempt usage with warnings (UsageResponse). */
export interface UsageResponseV2 {
  group_by: string;
  rows: UsageRowV2[];
  warnings: QueryWarning[];
}

// --- Costs ---

/** One grouped cost aggregate; dual metrics never merged (CostRowOut). */
export interface CostRowV2 {
  key: string;
  actual_spend_usd: string;
  subscription_value_usd: string;
  cost_priced_usd: string;
  cost_partial_usd: string;
  cost_estimated_usd: string;
  unpriced_event_count: number;
  /** ``mixed`` when the row spans several pricing versions. */
  pricing_version: string;
}

/** Grouped costs with warnings (CostResponse). */
export interface CostResponseV2 {
  group_by: string;
  rows: CostRowV2[];
  warnings: QueryWarning[];
}

/** Observed-versus-computed cost drift for one provider (ReconciliationRowOut). */
export interface ReconciliationRowV2 {
  provider: string;
  computed_usd: string;
  observed_usd: string;
  drift_usd: string;
  event_count: number;
}

/** Cost reconciliation drift rows (ReconciliationResponse). */
export interface ReconciliationResponseV2 {
  rows: ReconciliationRowV2[];
}

// --- Trace: attempts, requests, sessions ---

/** One final attempt event with lifecycle and usage fields (AttemptOut). */
export interface AttemptV2 {
  event_id: string;
  provider: string;
  native_model: string;
  requested_model: string | null;
  routed_model: string | null;
  ts_started: string;
  ts_completed: string | null;
  latency_ms: number | null;
  success: boolean;
  logical_request_id: string | null;
  session_id: string | null;
  source: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  reasoning_tokens: number;
  cost_usd: string | null;
}

/** A keyset-paginated page of attempts (AttemptsResponse). */
export interface AttemptsResponseV2 {
  attempts: AttemptV2[];
  next_cursor: string | null;
}

/** A logical request with its attempt-chain aggregates (RequestOut). */
export interface RequestV2 {
  provider: string;
  logical_request_id: string;
  requested_model: string | null;
  session_id: string | null;
  routing_policy: string | null;
  routing_reason: string | null;
  attempt_count: number;
  fallback_count: number;
  winning_attempt_id: string | null;
  ts_first: string | null;
  ts_last: string | null;
  total_tokens: number;
  cost_usd: string | null;
}

/** A keyset-paginated page of logical requests (RequestsResponse). */
export interface RequestsResponseV2 {
  requests: RequestV2[];
  next_cursor: string | null;
}

/** A logical request with its ordered attempts (RequestDetailResponse). */
export interface RequestDetailV2 {
  request: RequestV2;
  attempts: AttemptV2[];
}

/** A session rollup keyed by scoped identity (SessionOut). */
export interface SessionV2 {
  scoped_id: string;
  provider: string;
  source: string;
  session_id: string;
  attempt_count: number;
  total_tokens: number;
  cost_usd: string | null;
  ts_first: string | null;
  ts_last: string | null;
}

/** A paginated page of session rollups (SessionsResponse). */
export interface SessionsResponseV2 {
  sessions: SessionV2[];
  next_cursor: string | null;
}

// --- Sources ---

/** Query-time health of a reporting source (SourceHealthOut). */
export interface SourceHealthV2 {
  stale: boolean;
  last_successful_ingest: string | null;
  recent_error_count: number;
  reported_schema_version: number | null;
  clock_skew_seconds: number | null;
  staleness_threshold_seconds: number;
}

/** A reporting source with its health, no secrets (SourceOut). */
export interface SourceV2 {
  id: number;
  type: string;
  name: string;
  version: string | null;
  instance_id: string | null;
  machine: string | null;
  token_label: string | null;
  billing_mode: string;
  first_seen: string;
  last_seen: string;
  revoked: boolean;
  health: SourceHealthV2;
}

// --- Limits ---

/** One limit-utilization snapshot with provenance (LimitSnapshotOut). */
export interface LimitSnapshotV2 {
  id: number;
  provider: string;
  machine: string | null;
  ts: string;
  window_kind: string;
  utilization_pct: string;
  resets_at: string | null;
  /** ``official`` or ``estimated`` (FR-UI-012). */
  provenance: string;
}

/** A keyset-paginated page of limit snapshots (LimitsResponse). */
export interface LimitsResponseV2 {
  limits: LimitSnapshotV2[];
  next_cursor: string | null;
}

// --- Data quality ---

/** One recorded data-quality anomaly (DataQualityEventOut). */
export interface DataQualityEventV2 {
  id: number;
  kind: string;
  subject: string;
  detail: Record<string, unknown>;
  source_id: string | null;
  ts: string;
  resolved: boolean;
}

/** A keyset-paginated page of data-quality events (DataQualityResponse). */
export interface DataQualityResponseV2 {
  events: DataQualityEventV2[];
  next_cursor: string | null;
}

// --- Pricing (rate cards) ---

/** One stored rate card, the v2 per-unit pricing grain (RateCardOut). */
export interface RateCardV2 {
  id: number;
  provider: string;
  native_model: string;
  unit_type: string;
  effective_from: string;
  effective_to: string | null;
  currency: string;
  region: string | null;
  service_tier: string | null;
  mode: string;
  context_bracket: string | null;
  unit_price: string;
  source: string;
  priority: number;
  override: boolean;
}

// --- Pricing administration (mutations, reports) ---

/** Create a rate card (manual price or override) (RateCardCreateRequest). */
export interface RateCardCreate {
  provider: string;
  native_model: string;
  unit_type: string;
  effective_from: string;
  unit_price: string;
  currency?: string;
  mode?: string;
  service_tier?: string | null;
  context_bracket?: string | null;
  region?: string | null;
  source?: string;
  priority?: number;
  override?: boolean;
  effective_to?: string | null;
}

/** A created rate card plus the current pricing-state version (RateCardMutationResponse). */
export interface RateCardMutationResponse {
  rate_card: RateCardV2;
  pricing_version: string;
}

/** The outcome of closing a rate card (RateCardCloseResponse). */
export interface RateCardCloseResponse {
  rate_card_id: number;
  pricing_version: string;
}

/** One row's effect in an import diff (ImportChangeOut). */
export interface ImportChange {
  action: string;
  provider: string;
  native_model: string;
  unit_type: string;
  priority: number;
  new_price: string | null;
}

/** A rate-card import dry-run diff or apply result (ImportResponse, D-015). */
export interface ImportResponse {
  dry_run: boolean;
  digest: string;
  new: number;
  superseded: number;
  unchanged: number;
  conflicts: number;
  changes: ImportChange[];
}

/** The outcome of a reprice or revert operation (RepriceResponse). */
export interface RepriceResponse {
  pricing_version: string;
  affected: number;
}

/** An aggregate of unpriced/partial events for one model (UnpricedReportRow). */
export interface UnpricedReportRow {
  provider: string;
  native_model: string;
  cost_status: string;
  event_count: number;
}

/** An unknown-model observation recorded at ingest (UnknownModelReportRow). */
export interface UnknownModelReportRow {
  provider: string;
  native_model: string;
  observations: number;
  resolved: boolean;
  last_seen: string;
}

// --- Rollups ---

/** One daily_rollups row exposed for external tooling (RollupOut). */
export interface RollupV2 {
  id: number;
  day: string;
  provider: string;
  model: string;
  machine: string;
  project: string;
  source: string;
  environment: string;
  billing_mode: string;
  provenance: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cost_usd: string | null;
  cost_priced_usd: string | null;
  cost_partial_usd: string | null;
  cost_estimated_usd: string | null;
  unpriced_event_count: number;
  subscription_value_usd: string | null;
}

/** A keyset-paginated page of daily rollup rows (RollupsResponse). */
export interface RollupsResponseV2 {
  rollups: RollupV2[];
  next_cursor: string | null;
}
