// Typed client for the tokemetry query API.
//
// The bearer token is supplied per-instance (read from local storage by the
// app). All calls go through one request method so auth and error handling
// live in a single place.

import type {
  Block,
  CostResponse,
  HeatmapResponse,
  Limit,
  MachineSummary,
  Overview,
  PricingRow,
  SessionSummary,
  SummaryNow,
  UsageResponse,
} from './types';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface UsageQuery {
  groupBy: string;
  from?: string;
  to?: string;
  provider?: string;
  machine?: string;
  model?: string;
  project?: string;
}

export class ApiClient {
  constructor(
    private readonly token: string,
    private readonly baseUrl = '',
    // Wrap the global fetch so it is always invoked with the correct `this`
    // (window). Passing the bare `fetch` reference and calling it as a method
    // detaches it and native fetch throws "Illegal invocation".
    private readonly fetchFn: typeof fetch = (input, init) => fetch(input, init)
  ) {}

  private async request<T>(
    path: string,
    method = 'GET',
    body?: unknown
  ): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
    };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await this.fetchFn(this.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new ApiError(response.status, `request failed: ${response.status}`);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  summaryNow(): Promise<SummaryNow> {
    return this.request<SummaryNow>('/api/v1/summary/now');
  }

  summaryOverview(): Promise<Overview> {
    return this.request<Overview>('/api/v1/summary/overview');
  }

  limitsCurrent(): Promise<Limit[]> {
    return this.request<Limit[]>('/api/v1/limits/current');
  }

  limitsHistory(windowKind: string, hours = 24): Promise<Limit[]> {
    const params = new URLSearchParams({
      window_kind: windowKind,
      hours: String(hours),
    });
    return this.request<Limit[]>(`/api/v1/limits/history?${params}`);
  }

  blocks(hours = 120): Promise<Block[]> {
    return this.request<Block[]>(`/api/v1/blocks?hours=${hours}`);
  }

  usage(query: UsageQuery): Promise<UsageResponse> {
    return this.request<UsageResponse>(
      `/api/v1/usage?${buildUsageParams(query)}`
    );
  }

  sessions(limit = 100): Promise<SessionSummary[]> {
    return this.request<SessionSummary[]>(`/api/v1/sessions?limit=${limit}`);
  }

  machines(): Promise<MachineSummary[]> {
    return this.request<MachineSummary[]>('/api/v1/machines');
  }

  heatmap(from?: string, to?: string): Promise<HeatmapResponse> {
    return this.request<HeatmapResponse>(
      `/api/v1/heatmap?${rangeParams(from, to)}`
    );
  }

  cost(from?: string, to?: string): Promise<CostResponse> {
    return this.request<CostResponse>(`/api/v1/cost?${rangeParams(from, to)}`);
  }

  pricing(): Promise<PricingRow[]> {
    return this.request<PricingRow[]>('/api/v1/pricing');
  }

  createPrice(row: PriceRowInput): Promise<PricingRow> {
    return this.request<PricingRow>('/api/v1/pricing', 'POST', row);
  }

  syncLitellm(): Promise<{ synced: number }> {
    return this.request<{ synced: number }>(
      '/api/v1/pricing/sync-litellm',
      'POST',
      {}
    );
  }

  recomputeCosts(): Promise<{
    events_updated: number;
    rollups_refreshed: number;
  }> {
    return this.request<{ events_updated: number; rollups_refreshed: number }>(
      '/api/v1/pricing/recompute',
      'POST',
      {}
    );
  }

  rebuildRollups(): Promise<{ rollups_rebuilt: number }> {
    return this.request<{ rollups_rebuilt: number }>(
      '/api/v1/admin/rebuild-rollups',
      'POST',
      {}
    );
  }

  alertRules(): Promise<AlertRule[]> {
    return this.request<AlertRule[]>('/api/v1/alerts');
  }

  createAlertRule(rule: AlertRuleInput): Promise<AlertRule> {
    return this.request<AlertRule>('/api/v1/alerts', 'POST', rule);
  }

  updateAlertRule(id: number, rule: AlertRuleInput): Promise<AlertRule> {
    return this.request<AlertRule>(`/api/v1/alerts/${id}`, 'PUT', rule);
  }

  deleteAlertRule(id: number): Promise<void> {
    return this.request<void>(`/api/v1/alerts/${id}`, 'DELETE');
  }

  alertEvents(limit = 100): Promise<AlertEvent[]> {
    return this.request<AlertEvent[]>(`/api/v1/alerts/events?limit=${limit}`);
  }

  evaluateAlerts(): Promise<{ fired: AlertEvent[] }> {
    return this.request<{ fired: AlertEvent[] }>(
      '/api/v1/alerts/evaluate',
      'POST',
      {}
    );
  }

  testChannel(
    channel: string
  ): Promise<{ channel: string; delivered: boolean }> {
    return this.request<{ channel: string; delivered: boolean }>(
      `/api/v1/alerts/test/${encodeURIComponent(channel)}`,
      'POST',
      {}
    );
  }

  listTokens(): Promise<TokenInfo[]> {
    return this.request<TokenInfo[]>('/api/v1/tokens');
  }

  createToken(label: string): Promise<CreatedToken> {
    return this.request<CreatedToken>('/api/v1/tokens', 'POST', { label });
  }

  revokeToken(label: string): Promise<void> {
    return this.request<void>(
      `/api/v1/tokens/${encodeURIComponent(label)}`,
      'DELETE'
    );
  }
}

export interface TokenInfo {
  label: string;
  created_at: string;
  last_used: string | null;
  revoked: boolean;
}

export interface CreatedToken {
  label: string;
  token: string;
  created_at: string;
}

export interface AlertRule {
  id: number;
  name: string;
  kind: string;
  threshold: string | null;
  warn_threshold: string | null;
  crit_threshold: string | null;
  window_kind: string | null;
  channels: string[];
  cooldown_seconds: number;
  quiet_hours: Record<string, unknown> | null;
  enabled: boolean;
  config: Record<string, unknown>;
  state: string;
  last_fired_at: string | null;
}

export interface AlertRuleInput {
  name: string;
  kind: string;
  threshold?: string | null;
  warn_threshold?: string | null;
  crit_threshold?: string | null;
  window_kind?: string | null;
  channels: string[];
  cooldown_seconds: number;
  enabled: boolean;
}

export interface AlertEvent {
  id: number;
  rule_id: number;
  ts: string;
  severity: string;
  title: string;
  body: string;
  delivered: boolean;
  context: Record<string, unknown>;
}

export interface PriceRowInput {
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

export function buildUsageParams(query: UsageQuery): string {
  const params = new URLSearchParams({ group_by: query.groupBy });
  if (query.from) params.set('from', query.from);
  if (query.to) params.set('to', query.to);
  if (query.provider) params.set('provider', query.provider);
  if (query.machine) params.set('machine', query.machine);
  if (query.model) params.set('model', query.model);
  if (query.project) params.set('project', query.project);
  return params.toString();
}

function rangeParams(from?: string, to?: string): string {
  const params = new URLSearchParams();
  if (from) params.set('from', from);
  if (to) params.set('to', to);
  return params.toString();
}
