<script setup lang="ts">
// Requests and attempts trace, backed by /api/v2/requests and /api/v2/attempts.
// A logical request may span several attempts when routing falls back across
// models or providers; the request table expands to render that fallback chain
// as an ordered attempt timeline (FR-UI-004/005). A second tab lists raw
// attempts, and failure-rate and latency aggregates summarize the loaded
// attempts (FR-UI-006/007). Provider/model come from the global filter.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import StatTile from '@/components/StatTile.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { useGlobalFilters } from '@/composables/useGlobalFilters';
import { groupedBarOption } from '@/lib/charts';
import { formatCost, formatDateTime, formatTokens } from '@/lib/format';
import { knownModelIds, resolveModel } from '@/lib/modelRegistry';
import {
  failureRateBy,
  latencyValues,
  orderAttempts,
  percentile,
} from '@/lib/trace';
import { presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type { AttemptV2, ModelV2, ProviderV2, RequestV2 } from '@/api/types-v2';

type Tab = 'requests' | 'attempts';
type FailDim = 'provider' | 'model';

const { loading, error, run, retry } = useAsync();
const { provider: globalProvider } = useGlobalFilters();
const tab = ref<Tab>('requests');
const failDim = ref<FailDim>('provider');
const fallbackOnly = ref(false);

const requests = ref<RequestV2[]>([]);
const requestsCursor = ref<string | null>(null);
const attempts = ref<AttemptV2[]>([]);
const attemptsCursor = ref<string | null>(null);
const providers = ref<ProviderV2[]>([]);
const models = ref<ModelV2[]>([]);
const machines = ref<string[]>([]);
const filter = ref<UsageFilter>(presetRange('30d'));

// Fallback-chain expansion: which request is open, and its fetched attempts.
const expanded = ref<string | null>(null);
const chain = ref<AttemptV2[]>([]);

const knownIds = computed(() => knownModelIds(models.value));
const providerName = computed(() => {
  const map = new Map(providers.value.map((p) => [p.id, p.display_name]));
  return (id: string): string => map.get(id) ?? id;
});
const providerOptions = computed(() =>
  providers.value.map((p) => ({ value: p.id, label: p.display_name }))
);
const modelOptions = computed(() =>
  models.value
    .filter((m) => !globalProvider.value || m.provider === globalProvider.value)
    .map((m) => ({
      value: m.native_model_id,
      label: resolveModel(m.native_model_id, knownIds.value).display,
    }))
);

function modelDisplay(id: string | null): string {
  if (!id) return '—';
  return resolveModel(id, knownIds.value).display;
}

/** "1200" ms -> "1.2s"; sub-second stays in ms. */
function latencyText(ms: number | null): string {
  if (ms === null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

// Failure rate and latency summarize the attempts loaded so far.
const failureChart = computed(() => {
  const stats = failureRateBy(attempts.value, (a) =>
    failDim.value === 'provider'
      ? providerName.value(a.provider)
      : modelDisplay(a.native_model)
  );
  return groupedBarOption(
    stats.map((s) => s.key),
    [{ name: 'failure rate', values: stats.map((s) => s.rate * 100) }],
    { valueFormatter: (v) => `${Number(v).toFixed(1)}%` }
  );
});
const latency = computed(() => {
  const values = latencyValues(attempts.value);
  return {
    p50: latencyText(percentile(values, 50)),
    p95: latencyText(percentile(values, 95)),
    count: values.length,
  };
});

function rangeParams(): { from: string; to: string } {
  const fallback = presetRange('30d');
  return {
    from: `${filter.value.from ?? fallback.from}T00:00:00Z`,
    to: `${filter.value.to ?? fallback.to}T00:00:00Z`,
  };
}

async function loadRequests(append = false): Promise<void> {
  const client = useClient();
  const res = await client.v2Requests({
    ...rangeParams(),
    provider: filter.value.provider,
    model: filter.value.model,
    machine: filter.value.machine,
    fallbackOnly: fallbackOnly.value,
    cursor: append ? (requestsCursor.value ?? undefined) : undefined,
    limit: 50,
  });
  requests.value = append ? [...requests.value, ...res.requests] : res.requests;
  requestsCursor.value = res.next_cursor;
}

async function loadAttempts(append = false): Promise<void> {
  const client = useClient();
  const res = await client.v2Attempts({
    ...rangeParams(),
    provider: filter.value.provider,
    model: filter.value.model,
    machine: filter.value.machine,
    cursor: append ? (attemptsCursor.value ?? undefined) : undefined,
    limit: 100,
  });
  attempts.value = append ? [...attempts.value, ...res.attempts] : res.attempts;
  attemptsCursor.value = res.next_cursor;
}

async function reload(): Promise<void> {
  expanded.value = null;
  await run(async () => {
    await Promise.all([loadRequests(false), loadAttempts(false)]);
  });
}

async function toggleExpand(req: RequestV2): Promise<void> {
  if (expanded.value === req.logical_request_id) {
    expanded.value = null;
    return;
  }
  expanded.value = req.logical_request_id;
  chain.value = [];
  const detail = await useClient().v2RequestDetail(
    req.provider,
    req.logical_request_id
  );
  chain.value = orderAttempts(detail.attempts);
}

async function loadOptions(): Promise<void> {
  try {
    const client = useClient();
    providers.value = await client.v2Providers();
    models.value = await client.v2Models();
    machines.value = (await client.machines()).map((m) => m.id);
  } catch {
    providers.value = [];
  }
}

function onFilter(next: UsageFilter): void {
  filter.value = next;
  void reload();
}
function setFallbackOnly(value: boolean): void {
  fallbackOnly.value = value;
  void run(() => loadRequests(false));
}

onMounted(() => {
  void loadOptions();
  void reload();
});
</script>

<template>
  <div>
    <FilterBar
      :providers="providerOptions"
      :models="modelOptions"
      :machines="machines"
      @change="onFilter"
    />

    <section class="grid tiles">
      <StatTile
        label="Latency p50"
        :value="latency.p50"
        sub="loaded attempts"
      />
      <StatTile
        label="Latency p95"
        :value="latency.p95"
        sub="loaded attempts"
      />
      <StatTile
        label="Attempts sampled"
        :value="formatTokens(latency.count)"
        sub="for the aggregates below"
      />
    </section>

    <section class="card">
      <div class="toolbar">
        <h3>Failure rate by {{ failDim }}</h3>
        <div class="toggle">
          <button
            v-for="d in ['provider', 'model'] as FailDim[]"
            :key="d"
            :class="{ active: failDim === d }"
            @click="failDim = d"
          >
            {{ d }}
          </button>
        </div>
      </div>
      <EChart :option="failureChart" height="240px" />
      <p class="muted small">Over the {{ latency.count }} loaded attempts.</p>
    </section>

    <div class="tabs">
      <button :class="{ active: tab === 'requests' }" @click="tab = 'requests'">
        Requests
      </button>
      <button :class="{ active: tab === 'attempts' }" @click="tab = 'attempts'">
        Attempts
      </button>
      <label class="fallback">
        <input
          type="checkbox"
          :checked="fallbackOnly"
          @change="setFallbackOnly(($event.target as HTMLInputElement).checked)"
        />
        fallback chains only
      </label>
    </div>

    <AsyncState
      :loading="loading && requests.length === 0 && attempts.length === 0"
      :error="error"
      @retry="retry"
    >
      <section v-if="tab === 'requests'" class="card">
        <table class="data">
          <thead>
            <tr>
              <th>Request</th>
              <th>Provider</th>
              <th>Requested model</th>
              <th class="num">Attempts</th>
              <th class="num">Fallbacks</th>
              <th class="num">Tokens</th>
              <th class="num">Cost</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="req in requests" :key="req.logical_request_id">
              <tr class="row" @click="toggleExpand(req)">
                <td class="tabular">
                  <span class="caret">{{
                    expanded === req.logical_request_id ? '▾' : '▸'
                  }}</span>
                  {{ req.logical_request_id.slice(0, 12) }}
                </td>
                <td>{{ providerName(req.provider) }}</td>
                <td>{{ modelDisplay(req.requested_model) }}</td>
                <td class="num tabular">{{ req.attempt_count }}</td>
                <td class="num tabular">
                  <span
                    v-if="req.fallback_count > 0"
                    class="badge fallback-badge"
                  >
                    {{ req.fallback_count }}
                  </span>
                  <span v-else class="muted">0</span>
                </td>
                <td class="num tabular">
                  {{ formatTokens(req.total_tokens) }}
                </td>
                <td class="num tabular">{{ formatCost(req.cost_usd) }}</td>
                <td>{{ formatDateTime(req.ts_first) }}</td>
              </tr>
              <tr v-if="expanded === req.logical_request_id" class="chain-row">
                <td colspan="8">
                  <ol class="chain">
                    <li
                      v-for="(a, i) in chain"
                      :key="a.event_id"
                      class="attempt"
                      :class="{ won: a.event_id === req.winning_attempt_id }"
                    >
                      <span class="seq">#{{ i + 1 }}</span>
                      <span
                        class="dot"
                        :class="a.success ? 'ok' : 'fail'"
                        :title="a.success ? 'succeeded' : 'failed'"
                      ></span>
                      <span class="prov">{{ providerName(a.provider) }}</span>
                      <span class="model">
                        {{ modelDisplay(a.requested_model) }}
                        <template v-if="a.routed_model !== a.requested_model">
                          → {{ modelDisplay(a.routed_model) }}
                        </template>
                      </span>
                      <span class="muted">{{ latencyText(a.latency_ms) }}</span>
                      <span class="muted"
                        >{{
                          formatTokens(a.input_tokens + a.output_tokens)
                        }}
                        tok</span
                      >
                      <span
                        v-if="a.event_id === req.winning_attempt_id"
                        class="badge won-badge"
                        >winner</span
                      >
                    </li>
                    <li v-if="chain.length === 0" class="muted">
                      Loading chain…
                    </li>
                  </ol>
                </td>
              </tr>
            </template>
            <tr v-if="requests.length === 0">
              <td colspan="8" class="muted">No requests in this range.</td>
            </tr>
          </tbody>
        </table>
        <button
          v-if="requestsCursor"
          class="more"
          @click="run(() => loadRequests(true))"
        >
          Load more
        </button>
      </section>

      <section v-else class="card">
        <table class="data">
          <thead>
            <tr>
              <th>Started</th>
              <th>Provider</th>
              <th>Model</th>
              <th>Outcome</th>
              <th class="num">Latency</th>
              <th class="num">Tokens</th>
              <th class="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in attempts" :key="a.event_id">
              <td>{{ formatDateTime(a.ts_started) }}</td>
              <td>{{ providerName(a.provider) }}</td>
              <td>{{ modelDisplay(a.native_model) }}</td>
              <td>
                <span class="dot" :class="a.success ? 'ok' : 'fail'"></span>
                {{ a.success ? 'ok' : 'failed' }}
              </td>
              <td class="num tabular">{{ latencyText(a.latency_ms) }}</td>
              <td class="num tabular">
                {{ formatTokens(a.input_tokens + a.output_tokens) }}
              </td>
              <td class="num tabular">{{ formatCost(a.cost_usd) }}</td>
            </tr>
            <tr v-if="attempts.length === 0">
              <td colspan="7" class="muted">No attempts in this range.</td>
            </tr>
          </tbody>
        </table>
        <button
          v-if="attemptsCursor"
          class="more"
          @click="run(() => loadAttempts(true))"
        >
          Load more
        </button>
      </section>
    </AsyncState>
  </div>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
h3 {
  margin: 0 0 1rem;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.small {
  font-size: 0.8rem;
  margin: 0.5rem 0 0;
}
.tabs {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.75rem;
}
.tabs button,
.toggle button {
  font: inherit;
  padding: 0.35rem 0.8rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-secondary);
  cursor: pointer;
}
.tabs button.active,
.toggle button.active {
  background: var(--gridline);
  color: var(--text-primary);
}
.toggle {
  display: flex;
  gap: 0.25rem;
}
.fallback {
  margin-left: auto;
  font-size: 0.85rem;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.data {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
.data th,
.data td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.data th.num,
.data td.num {
  text-align: right;
}
.row {
  cursor: pointer;
}
.row:hover {
  background: var(--gridline);
}
.caret {
  color: var(--text-muted);
  margin-right: 0.25rem;
}
.chain-row td {
  background: color-mix(in srgb, var(--gridline) 40%, transparent);
}
.chain {
  list-style: none;
  margin: 0;
  padding: 0.5rem 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.attempt {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.88rem;
}
.attempt .seq {
  color: var(--text-muted);
  width: 2rem;
}
.attempt.won {
  font-weight: 600;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.dot.ok {
  background: var(--status-good);
}
.dot.fail {
  background: var(--status-critical);
}
.badge {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.05rem 0.4rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.fallback-badge {
  color: var(--status-warning);
}
.won-badge {
  color: var(--status-good);
}
.more {
  margin-top: 0.75rem;
  font: inherit;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  cursor: pointer;
}
</style>
