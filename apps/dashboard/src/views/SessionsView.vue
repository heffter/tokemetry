<script setup lang="ts">
// Sessions: a navigable table — sortable, filterable (by machine/project/
// provider, honoring a ?machine= query from the Machines page), with a top-N
// chart, inline token magnitude bars, and a totals footer.
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import Sparkline from '@/components/Sparkline.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { barOption } from '@/lib/charts';
import {
  formatCost,
  formatDateTime,
  formatPct,
  formatTokens,
  timeAgo,
} from '@/lib/format';
import type { Anomaly, SessionDetail, SessionSummary } from '@/api/types';

const route = useRoute();
const { loading, error, run, retry } = useAsync();
const sessions = ref<SessionSummary[]>([]);
const anomalies = ref<Anomaly[]>([]);
const enoughData = ref(true);

// Map session id -> its anomaly, for row flags and the insights card.
const anomalyById = computed(
  () => new Map(anomalies.value.map((a) => [a.session_id, a]))
);
const topAnomalies = computed(() => anomalies.value.slice(0, 6));

const expandedId = ref<string | null>(null);
const detail = ref<SessionDetail | null>(null);
const detailLoading = ref(false);

// Running cumulative token total per turn, for the drill-down sparkline.
const detailCumulative = computed(() => {
  if (!detail.value) return [];
  let sum = 0;
  return detail.value.events.map((e) => (sum += e.total_tokens));
});

async function toggleDetail(id: string): Promise<void> {
  if (expandedId.value === id) {
    expandedId.value = null;
    detail.value = null;
    return;
  }
  expandedId.value = id;
  detail.value = null;
  detailLoading.value = true;
  try {
    detail.value = await useClient().sessionDetail(id);
  } finally {
    detailLoading.value = false;
  }
}

const machineFilter = ref<string>((route.query.machine as string) ?? '');
const projectFilter = ref<string>('');
const providerFilter = ref<string>('');

type SortKey = 'total_tokens' | 'cost_usd' | 'message_count' | 'last_at';
const sortKey = ref<SortKey>('total_tokens');
const sortDir = ref<'asc' | 'desc'>('desc');

const machines = computed(() =>
  [...new Set(sessions.value.map((s) => s.machine).filter(Boolean))].sort()
);
const projects = computed(() =>
  [...new Set(sessions.value.map((s) => s.project).filter(Boolean))].sort()
);
const providers = computed(() =>
  [...new Set(sessions.value.map((s) => s.provider))].sort()
);

const filtered = computed(() =>
  sessions.value.filter(
    (s) =>
      (!machineFilter.value || s.machine === machineFilter.value) &&
      (!projectFilter.value || s.project === projectFilter.value) &&
      (!providerFilter.value || s.provider === providerFilter.value)
  )
);

const sorted = computed(() => {
  const dir = sortDir.value === 'asc' ? 1 : -1;
  const key = sortKey.value;
  return [...filtered.value].sort((a, b) => {
    const av = key === 'cost_usd' ? Number(a.cost_usd ?? 0) : Number(a[key]);
    const bv = key === 'cost_usd' ? Number(b.cost_usd ?? 0) : Number(b[key]);
    if (key === 'last_at') {
      return (
        (new Date(a.last_at).getTime() - new Date(b.last_at).getTime()) * dir
      );
    }
    return (av - bv) * dir;
  });
});

const maxTokens = computed(() =>
  Math.max(1, ...filtered.value.map((s) => s.total_tokens))
);

const totals = computed(() => ({
  count: filtered.value.length,
  tokens: filtered.value.reduce((sum, s) => sum + s.total_tokens, 0),
  messages: filtered.value.reduce((sum, s) => sum + s.message_count, 0),
  cost: filtered.value.reduce((sum, s) => sum + Number(s.cost_usd ?? 0), 0),
}));

const topChart = computed(() => {
  const top = [...filtered.value]
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .slice(0, 10);
  return barOption(
    top.map((s) => `${s.project ?? s.session_id.slice(0, 8)}`),
    top.map((s) => s.total_tokens),
    'tokens'
  );
});

function sortBy(key: SortKey): void {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
  } else {
    sortKey.value = key;
    sortDir.value = 'desc';
  }
}

function arrow(key: SortKey): string {
  if (sortKey.value !== key) return '';
  return sortDir.value === 'desc' ? ' ▾' : ' ▴';
}

async function load(): Promise<void> {
  await run(async () => {
    sessions.value = await useClient().sessions(1000);
  });
}

async function loadAnomalies(): Promise<void> {
  try {
    const report = await useClient().insightsAnomalies();
    enoughData.value = report.enough_data;
    anomalies.value = report.anomalies;
  } catch {
    anomalies.value = [];
  }
}

onMounted(() => {
  void load();
  void loadAnomalies();
});
</script>

<template>
  <AsyncState
    :loading="loading && sessions.length === 0"
    :error="error"
    :empty="!loading && sessions.length === 0"
    empty-text="No sessions yet."
    @retry="retry"
  >
    <section class="card">
      <div class="head">
        <h3>Sessions</h3>
        <div class="filters">
          <select v-model="machineFilter">
            <option value="">all machines</option>
            <option v-for="m in machines" :key="m ?? ''" :value="m">
              {{ m }}
            </option>
          </select>
          <select v-model="projectFilter">
            <option value="">all projects</option>
            <option v-for="p in projects" :key="p ?? ''" :value="p">
              {{ p }}
            </option>
          </select>
          <select v-model="providerFilter">
            <option value="">all providers</option>
            <option v-for="p in providers" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
      </div>

      <EChart :option="topChart" height="220px" />

      <div v-if="topAnomalies.length" class="insights">
        <h4>Insights — sessions that stand out</h4>
        <ul>
          <li
            v-for="a in topAnomalies"
            :key="a.session_id"
            class="insight clickable"
            @click="toggleDetail(a.session_id)"
          >
            <span class="mono">{{ a.session_id.slice(0, 8) }}</span>
            <span>{{ a.project ?? '—' }}</span>
            <span class="tabular">{{ formatTokens(a.total_tokens) }}</span>
            <span class="reasons">
              <span v-for="r in a.reasons" :key="r" class="chip warn">{{
                r
              }}</span>
            </span>
          </li>
        </ul>
      </div>
      <p v-else-if="!enoughData" class="muted small note">
        Anomaly detection needs at least 20 sessions to learn your baseline.
      </p>

      <table>
        <thead>
          <tr>
            <th>Session</th>
            <th>Project</th>
            <th>Machine</th>
            <th class="num sortable" @click="sortBy('message_count')">
              Messages{{ arrow('message_count') }}
            </th>
            <th class="num sortable" @click="sortBy('total_tokens')">
              Tokens{{ arrow('total_tokens') }}
            </th>
            <th class="num sortable" @click="sortBy('cost_usd')">
              Cost{{ arrow('cost_usd') }}
            </th>
            <th class="sortable" @click="sortBy('last_at')">
              Last active{{ arrow('last_at') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in sorted" :key="s.session_id">
            <tr class="clickable" @click="toggleDetail(s.session_id)">
              <td class="mono" :title="s.session_id">
                {{ expandedId === s.session_id ? '▾' : '▸' }}
                {{ s.session_id.slice(0, 8) }}
                <span
                  v-if="anomalyById.has(s.session_id)"
                  class="flag"
                  :title="anomalyById.get(s.session_id)?.reasons.join(', ')"
                  >flagged</span
                >
              </td>
              <td>{{ s.project ?? '—' }}</td>
              <td>{{ s.machine ?? '—' }}</td>
              <td class="num tabular">{{ s.message_count }}</td>
              <td class="num tabular">
                <div class="magnitude">
                  <div
                    class="mbar"
                    :style="{ width: `${(s.total_tokens / maxTokens) * 100}%` }"
                  ></div>
                  <span>{{ formatTokens(s.total_tokens) }}</span>
                </div>
              </td>
              <td class="num tabular">
                <span
                  v-if="s.cost_usd === null"
                  class="unpriced"
                  title="No price for this model — add it in Settings"
                  >unpriced</span
                >
                <template v-else>{{ formatCost(s.cost_usd) }}</template>
              </td>
              <td :title="formatDateTime(s.last_at)">
                {{ timeAgo(s.last_at) }}
              </td>
            </tr>
            <tr v-if="expandedId === s.session_id" class="detail-row">
              <td :colspan="7">
                <div v-if="detailLoading" class="muted">loading…</div>
                <div v-else-if="detail" class="detail">
                  <div class="chips">
                    <span class="chip">
                      {{
                        formatTokens(Math.round(detail.stats.tokens_per_turn))
                      }}
                      /turn
                    </span>
                    <span class="chip">
                      {{ formatPct(detail.stats.cache_hit_rate * 100) }} cache
                      hit
                    </span>
                    <span class="chip">
                      context {{ detail.stats.context_growth.toFixed(1) }}x
                    </span>
                    <span
                      v-if="detail.stats.inflection_index !== null"
                      class="chip warn"
                    >
                      consider /clear at turn
                      {{ detail.stats.inflection_index + 1 }}
                    </span>
                  </div>
                  <Sparkline
                    v-if="detailCumulative.length > 1"
                    :values="detailCumulative"
                    :max="detailCumulative[detailCumulative.length - 1]"
                    :marker-index="detail.stats.inflection_index"
                    color="var(--series-1, #2a78d6)"
                    :width="360"
                    :height="48"
                  />
                  <span class="muted small">cumulative tokens over turns</span>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
        <tfoot>
          <tr>
            <td :colspan="3">{{ totals.count }} sessions</td>
            <td class="num tabular">{{ totals.messages }}</td>
            <td class="num tabular">{{ formatTokens(totals.tokens) }}</td>
            <td class="num tabular">{{ formatCost(String(totals.cost)) }}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </section>
  </AsyncState>
</template>

<style scoped>
h3 {
  margin: 0;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.filters {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
select {
  font: inherit;
  padding: 0.35rem 0.5rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
  margin-top: 1rem;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}
.num {
  text-align: right;
}
.sortable {
  cursor: pointer;
  user-select: none;
}
.sortable:hover {
  color: var(--text-primary);
}
.clickable {
  cursor: pointer;
}
.clickable:hover td {
  background: color-mix(in srgb, var(--gridline) 40%, transparent);
}
.detail-row td {
  background: var(--page);
}
.detail {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.25rem 0;
}
.chips {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.chip {
  font-size: 0.8rem;
  padding: 0.15rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
}
.chip.warn {
  color: var(--status-warning);
  border-color: var(--status-warning);
}
.small {
  font-size: 0.75rem;
}
.note {
  margin: 1rem 0 0;
}
.insights {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius, 10px);
  background: var(--page);
}
.insights h4 {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
}
.insights ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.insight {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--border);
}
.insight .reasons {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
  margin-left: auto;
}
.flag {
  font-size: 0.7rem;
  font-weight: 700;
  color: var(--status-warning);
  text-transform: uppercase;
  margin-left: 0.3rem;
}
tfoot td {
  font-weight: 600;
  border-bottom: none;
}
.mono {
  font-family: ui-monospace, monospace;
}
.magnitude {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
}
.mbar {
  height: 8px;
  border-radius: 4px;
  background: var(--series-1);
  max-width: 80px;
  flex: 1;
}
.unpriced {
  font-size: 0.75rem;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.05rem 0.35rem;
}
</style>
