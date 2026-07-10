// Response shapes mirroring the tokemetry server query API.

export interface UsageBucket {
  key: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  total_tokens: number;
  cost_usd: string | null;
}

export interface UsageResponse {
  group_by: string;
  start: string;
  end: string;
  buckets: UsageBucket[];
}

export interface Limit {
  provider: string;
  window_kind: string;
  utilization_pct: number;
  resets_at: string | null;
  ts: string;
  provenance: string;
  age_seconds: number;
  derived_reset: boolean;
}

export interface Prediction {
  window_kind: string;
  utilization_pct: number;
  slope_pct_per_min: number;
  predicted_exhaustion_at: string | null;
  resets_at: string | null;
}

export interface TodaySummary {
  total_tokens: number;
  cost_usd: string | null;
  by_model: UsageBucket[];
}

export interface SummaryNow {
  now: string;
  limits: Limit[];
  token_burn_rate_per_min: number;
  prediction: Prediction | null;
  today: TodaySummary;
}

export interface Overview {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  total_tokens: number;
  cost_usd: string | null;
  session_count: number;
  machine_count: number;
  first_event: string | null;
  last_event: string | null;
}

export interface Block {
  start: string;
  end: string;
  total_tokens: number;
  cost_usd: string | null;
  peak_tokens_per_min: number;
  end_utilization_pct: number | null;
}

export interface SessionSummary {
  session_id: string;
  provider: string;
  machine: string | null;
  project: string | null;
  started_at: string;
  last_at: string;
  message_count: number;
  total_tokens: number;
  cost_usd: string | null;
}

export interface SessionEvent {
  ts: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_short_tokens: number;
  cache_write_long_tokens: number;
  total_tokens: number;
  cost_usd: string | null;
}

export interface SessionStats {
  tokens_per_turn: number;
  cache_hit_rate: number;
  context_growth: number;
  inflection_index: number | null;
}

export interface SessionDetail {
  session_id: string;
  project: string | null;
  machine: string | null;
  message_count: number;
  total_tokens: number;
  events: SessionEvent[];
  stats: SessionStats;
}

export interface Scorecard {
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_rate: number;
  verbosity_ratio: number;
  median_tokens_per_turn: number;
  sidechain_share: number;
  unattributed_share: number;
  session_count: number;
  machine_count: number;
  top_models: [string, number][];
}

export interface ReportDimension {
  name: string;
  total_tokens: number;
  cache_hit_rate: number;
  median_tokens_per_turn: number;
  verbosity_ratio: number;
  sidechain_share: number;
  session_count: number;
}

export interface Recommendation {
  id: string;
  title: string;
  severity: string;
  evidence: string;
  affected: string[];
  impact_tokens: number | null;
  effort: string;
}

export interface Report {
  start: string;
  end: string;
  scorecard: Scorecard;
  projects: ReportDimension[];
  machines: ReportDimension[];
  trend: [string, number][];
  recommendations: Recommendation[];
}

export interface Anomaly {
  session_id: string;
  project: string | null;
  reasons: string[];
  severity_score: number;
  total_tokens: number;
  cost_usd: number | null;
  cache_hit_rate: number;
}

export interface AnomalyReport {
  enough_data: boolean;
  session_count: number;
  anomalies: Anomaly[];
}

export interface MachineSummary {
  id: string;
  platform: string | null;
  last_seen: string | null;
  collector_version: string | null;
  total_tokens: number;
  event_count: number;
}

export interface PunchCell {
  weekday: number;
  hour: number;
  total_tokens: number;
}

export interface HeatmapResponse {
  calendar: UsageBucket[];
  punch_card: PunchCell[];
}

export interface CostResponse {
  start: string;
  end: string;
  total_cost_usd: string;
  subscription_monthly_usd: number | null;
  value_multiple: number | null;
}

export interface PricingRow {
  provider: string;
  model: string;
  effective_date: string;
  input_per_mtok: string;
  output_per_mtok: string;
  cache_read_per_mtok: string;
  cache_write_short_per_mtok: string;
  cache_write_long_per_mtok: string;
  source: string;
}

export interface StreamMessage {
  type: string;
  machine: string;
  accepted: number;
}
