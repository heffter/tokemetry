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
    private readonly fetchFn: typeof fetch = fetch
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
