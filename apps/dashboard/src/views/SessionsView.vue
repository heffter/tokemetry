<script setup lang="ts">
// Sessions: a navigable table — sortable, filterable (by machine/project/
// provider, honoring a ?machine= query from the Machines page), with a top-N
// chart, inline token magnitude bars, and a totals footer.
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { barOption } from '@/lib/charts';
import {
  formatCost,
  formatDateTime,
  formatTokens,
  timeAgo,
} from '@/lib/format';
import type { SessionSummary } from '@/api/types';

const route = useRoute();
const { loading, error, run, retry } = useAsync();
const sessions = ref<SessionSummary[]>([]);

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

onMounted(load);
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
          <tr v-for="s in sorted" :key="s.session_id">
            <td class="mono" :title="s.session_id">
              {{ s.session_id.slice(0, 8) }}
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
  background: #2a78d6;
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
