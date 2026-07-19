// Typed client for the tokemetry query API.
//
// The bearer token is supplied per-instance (read from local storage by the
// app). All calls go through one request method so auth and error handling
// live in a single place.

import type {
  AnomalyReport,
  Block,
  CostResponse,
  HeatmapResponse,
  Limit,
  MachineSummary,
  Overview,
  PricingRow,
  Report,
  SessionDetail,
  SessionSummary,
  SummaryNow,
  UsageResponse,
} from './types';
import type {
  AttemptsResponseV2,
  CostResponseV2,
  DataQualityResponseV2,
  LimitsResponseV2,
  ModelLifecycle,
  ModelV2,
  ProviderV2,
  RateCardV2,
  ReconciliationResponseV2,
  RequestDetailV2,
  RequestsResponseV2,
  RollupsResponseV2,
  RollupV2,
  SessionsResponseV2,
  SessionV2,
  SourceV2,
  UsageResponseV2,
} from './types-v2';

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
      // Surface the server's error detail (FastAPI returns {detail: ...}) so
      // callers can show why a request failed, not just the status code.
      let detail = '';
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body?.detail === 'string') detail = `: ${body.detail}`;
      } catch {
        // Non-JSON error body; the status alone will have to do.
      }
      throw new ApiError(
        response.status,
        `request failed (${response.status})${detail}`
      );
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

  sessionDetail(id: string): Promise<SessionDetail> {
    return this.request<SessionDetail>(
      `/api/v1/sessions/${encodeURIComponent(id)}`
    );
  }

  insightsAnomalies(): Promise<AnomalyReport> {
    return this.request<AnomalyReport>('/api/v1/insights/anomalies');
  }

  report(from?: string, to?: string): Promise<Report> {
    return this.request<Report>(`/api/v1/report?${rangeParams(from, to)}`);
  }

  // The export is a Markdown document, not JSON, so it bypasses request<T>()
  // and returns raw text for the caller to save as a file.
  async reportExport(
    size: 'compact' | 'full',
    from?: string,
    to?: string
  ): Promise<string> {
    const params = new URLSearchParams(rangeParams(from, to));
    params.set('size', size);
    const response = await this.fetchFn(
      `${this.baseUrl}/api/v1/report/export?${params}`,
      {
        headers: { Authorization: `Bearer ${this.token}` },
      }
    );
    if (!response.ok) {
      throw new ApiError(response.status, `export failed (${response.status})`);
    }
    return response.text();
  }

  machines(): Promise<MachineSummary[]> {
    return this.request<MachineSummary[]>('/api/v1/machines');
  }

  heatmap(
    from?: string,
    to?: string,
    machine?: string,
    project?: string
  ): Promise<HeatmapResponse> {
    const params = new URLSearchParams();
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    if (machine) params.set('machine', machine);
    if (project) params.set('project', project);
    return this.request<HeatmapResponse>(`/api/v1/heatmap?${params}`);
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

  getChannels(): Promise<ChannelsResponse> {
    return this.request<ChannelsResponse>('/api/v1/alerts/channels');
  }

  putChannel(
    name: string,
    fields: Record<string, string>
  ): Promise<ChannelsResponse> {
    return this.request<ChannelsResponse>(
      `/api/v1/alerts/channels/${encodeURIComponent(name)}`,
      'PUT',
      fields
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

  // --- Provider-neutral v2 read API (scope query:read) -------------------
  // Thin typed wrappers over the /api/v2 read endpoints. Registry data
  // (providers, models) drives the UI so no view hardcodes a provider set
  // (NFR-MAIN-006); the query endpoints all take the uniform filter surface
  // built by buildV2Filters (FR-QUERY-002/011).

  v2Providers(): Promise<ProviderV2[]> {
    return this.request<ProviderV2[]>('/api/v2/providers');
  }

  v2Models(
    opts: {
      provider?: string;
      lifecycle?: ModelLifecycle;
    } = {}
  ): Promise<ModelV2[]> {
    const params = new URLSearchParams();
    if (opts.provider) params.set('provider', opts.provider);
    if (opts.lifecycle) params.set('lifecycle', opts.lifecycle);
    return this.request<ModelV2[]>(`/api/v2/models?${params}`);
  }

  v2Usage(query: V2GroupedQuery): Promise<UsageResponseV2> {
    return this.request<UsageResponseV2>(
      `/api/v2/usage?${buildV2GroupedParams(query)}`
    );
  }

  v2Costs(query: V2GroupedQuery): Promise<CostResponseV2> {
    return this.request<CostResponseV2>(
      `/api/v2/costs?${buildV2GroupedParams(query)}`
    );
  }

  v2Reconciliation(query: V2RangeQuery): Promise<ReconciliationResponseV2> {
    const params = new URLSearchParams({ from: query.from, to: query.to });
    return this.request<ReconciliationResponseV2>(
      `/api/v2/costs/reconciliation?${params}`
    );
  }

  v2Attempts(
    query: V2PageQuery & { logicalRequestId?: string }
  ): Promise<AttemptsResponseV2> {
    const params = buildV2RangeParams(query);
    appendV2Page(params, query);
    if (query.logicalRequestId) {
      params.set('logical_request_id', query.logicalRequestId);
    }
    return this.request<AttemptsResponseV2>(`/api/v2/attempts?${params}`);
  }

  v2Requests(
    query: V2PageQuery & { routingPolicy?: string; fallbackOnly?: boolean }
  ): Promise<RequestsResponseV2> {
    const params = buildV2RangeParams(query);
    appendV2Page(params, query);
    if (query.routingPolicy) params.set('routing_policy', query.routingPolicy);
    if (query.fallbackOnly) params.set('fallback_only', 'true');
    return this.request<RequestsResponseV2>(`/api/v2/requests?${params}`);
  }

  v2RequestDetail(
    provider: string,
    logicalRequestId: string
  ): Promise<RequestDetailV2> {
    return this.request<RequestDetailV2>(
      `/api/v2/requests/${encodeURIComponent(provider)}/` +
        encodeURIComponent(logicalRequestId)
    );
  }

  v2Sessions(query: V2PageQuery): Promise<SessionsResponseV2> {
    const params = buildV2RangeParams(query);
    appendV2Page(params, query);
    return this.request<SessionsResponseV2>(`/api/v2/sessions?${params}`);
  }

  v2SessionDetail(scopedId: string): Promise<SessionV2> {
    return this.request<SessionV2>(
      `/api/v2/sessions/${encodeURIComponent(scopedId)}`
    );
  }

  v2Sources(
    opts: { type?: string; stale?: boolean } = {}
  ): Promise<SourceV2[]> {
    const params = new URLSearchParams();
    if (opts.type) params.set('type', opts.type);
    if (opts.stale !== undefined) params.set('stale', String(opts.stale));
    return this.request<SourceV2[]>(`/api/v2/sources?${params}`);
  }

  v2UpdateSource(
    id: number,
    fields: { tokenLabel?: string; billingMode?: string }
  ): Promise<SourceV2> {
    const body: Record<string, string> = {};
    if (fields.tokenLabel !== undefined) body.token_label = fields.tokenLabel;
    if (fields.billingMode !== undefined)
      body.billing_mode = fields.billingMode;
    return this.request<SourceV2>(`/api/v2/sources/${id}`, 'PATCH', body);
  }

  v2Limits(
    query: V2PageQuery & { windowKind?: string; provenance?: string }
  ): Promise<LimitsResponseV2> {
    const params = new URLSearchParams({ from: query.from, to: query.to });
    if (query.provider) params.set('provider', query.provider);
    if (query.machine) params.set('machine', query.machine);
    if (query.windowKind) params.set('window_kind', query.windowKind);
    if (query.provenance) params.set('provenance', query.provenance);
    appendV2Page(params, query);
    return this.request<LimitsResponseV2>(`/api/v2/limits?${params}`);
  }

  v2DataQuality(
    query: {
      kind?: string;
      subject?: string;
      source?: string;
      resolved?: boolean;
      limit?: number;
      cursor?: string;
    } = {}
  ): Promise<DataQualityResponseV2> {
    const params = new URLSearchParams();
    if (query.kind) params.set('kind', query.kind);
    if (query.subject) params.set('subject', query.subject);
    if (query.source) params.set('source', query.source);
    if (query.resolved !== undefined) {
      params.set('resolved', String(query.resolved));
    }
    appendV2Page(params, query);
    return this.request<DataQualityResponseV2>(
      `/api/v2/data-quality?${params}`
    );
  }

  v2Pricing(
    query: {
      provider?: string;
      nativeModel?: string;
      unitType?: string;
      activeOn?: string;
    } = {}
  ): Promise<RateCardV2[]> {
    const params = new URLSearchParams();
    if (query.provider) params.set('provider', query.provider);
    if (query.nativeModel) params.set('native_model', query.nativeModel);
    if (query.unitType) params.set('unit_type', query.unitType);
    if (query.activeOn) params.set('active_on', query.activeOn);
    return this.request<RateCardV2[]>(`/api/v2/pricing?${params}`);
  }

  v2Rollups(
    query: V2PageQuery & { environment?: string; billingMode?: string }
  ): Promise<RollupsResponseV2> {
    const params = new URLSearchParams({ from: query.from, to: query.to });
    if (query.provider) params.set('provider', query.provider);
    if (query.model) params.set('model', query.model);
    if (query.machine) params.set('machine', query.machine);
    if (query.source) params.set('source', query.source);
    if (query.environment) params.set('environment', query.environment);
    if (query.billingMode) params.set('billing_mode', query.billingMode);
    appendV2Page(params, query);
    return this.request<RollupsResponseV2>(`/api/v2/rollups?${params}`);
  }

  /** Fetch every rollup page for a range, following next_cursor.
   *
   * The rollups endpoint is keyset-paginated; views that aggregate a whole
   * range client-side (the trend/breakdown charts) need every page. maxPages is
   * a runaway guard well above any realistic row count for a bounded span. */
  async v2AllRollups(
    query: V2PageQuery & { environment?: string; billingMode?: string },
    maxPages = 100
  ): Promise<RollupV2[]> {
    const rows: RollupV2[] = [];
    let cursor = query.cursor;
    for (let page = 0; page < maxPages; page += 1) {
      const res = await this.v2Rollups({
        ...query,
        limit: query.limit ?? 200,
        cursor,
      });
      rows.push(...res.rollups);
      if (!res.next_cursor) break;
      cursor = res.next_cursor;
    }
    return rows;
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

export interface ChannelField {
  name: string;
  value: string;
  is_secret: boolean;
  is_set: boolean;
}

export interface Channel {
  name: string;
  configured: boolean;
  fields: ChannelField[];
}

export interface ChannelsResponse {
  channels: Channel[];
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

/** The uniform v2 dimension and pseudo-filter surface (FR-QUERY-002/011).
 *
 * ``model`` and ``session`` map to the server's ``model``/``session`` query
 * aliases; the ``unknown*`` flags select events whose provider/model is not in
 * the registry. Camel-cased flags become snake_case query params. */
export interface V2Filters {
  provider?: string;
  model?: string;
  source?: string;
  machine?: string;
  project?: string;
  session?: string;
  environment?: string;
  outcome?: string;
  unknownProvider?: boolean;
  unknownModel?: boolean;
}

/** A bounded time range plus the uniform filters (every v2 query needs a range). */
export interface V2RangeQuery extends V2Filters {
  from: string;
  to: string;
}

/** A grouped aggregate query (/usage, /costs): range, filters, group_by, sort. */
export interface V2GroupedQuery extends V2RangeQuery {
  groupBy?: string;
  sort?: string;
}

/** A keyset-paginated query: range, filters, and page controls. */
export interface V2PageQuery extends V2RangeQuery {
  limit?: number;
  cursor?: string;
}

/** Append the uniform filter params to ``params``, omitting unset ones. */
export function appendV2Filters(
  params: URLSearchParams,
  filters: V2Filters
): void {
  if (filters.provider) params.set('provider', filters.provider);
  if (filters.model) params.set('model', filters.model);
  if (filters.source) params.set('source', filters.source);
  if (filters.machine) params.set('machine', filters.machine);
  if (filters.project) params.set('project', filters.project);
  if (filters.session) params.set('session', filters.session);
  if (filters.environment) params.set('environment', filters.environment);
  if (filters.outcome) params.set('outcome', filters.outcome);
  if (filters.unknownProvider) params.set('unknown_provider', 'true');
  if (filters.unknownModel) params.set('unknown_model', 'true');
}

/** Append keyset page controls (limit, cursor), omitting unset ones. */
export function appendV2Page(
  params: URLSearchParams,
  page: { limit?: number; cursor?: string }
): void {
  if (page.limit !== undefined) params.set('limit', String(page.limit));
  if (page.cursor) params.set('cursor', page.cursor);
}

/** Build the query string for a range + uniform-filter v2 request. */
export function buildV2RangeParams(query: V2RangeQuery): URLSearchParams {
  const params = new URLSearchParams({ from: query.from, to: query.to });
  appendV2Filters(params, query);
  return params;
}

/** Build the query string for a grouped aggregate v2 request (/usage, /costs). */
export function buildV2GroupedParams(query: V2GroupedQuery): string {
  const params = buildV2RangeParams(query);
  if (query.groupBy) params.set('group_by', query.groupBy);
  if (query.sort) params.set('sort', query.sort);
  return params.toString();
}
