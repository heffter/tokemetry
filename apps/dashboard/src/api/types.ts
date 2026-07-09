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
