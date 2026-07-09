<script setup lang="ts">
// Daily usage trend as a real stacked area over the five token components,
// plus a categorical breakdown by a chosen dimension. An all-time summary
// strip sits on top and a shared FilterBar drives the date range and the
// machine/project scope.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import StatTile from '@/components/StatTile.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  stackedAreaOption,
  stackedTokenBarOption,
  TOKEN_COMPONENTS,
} from '@/lib/charts';
import {
  cacheReadShare,
  formatCost,
  formatTokens,
  modelLabel,
} from '@/lib/format';
import { presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type { Overview, UsageBucket } from '@/api/types';

const { loading, error, run, retry } = useAsync();
const dimension = ref<'model' | 'machine' | 'project'>('model');
const dims = ['model', 'machine', 'project'] as const;
const dayBuckets = ref<UsageBucket[]>([]);
const dimBuckets = ref<UsageBucket[]>([]);
const overview = ref<Overview | null>(null);
const machines = ref<string[]>([]);
const projects = ref<string[]>([]);
// Default matches the FilterBar's initial 30d preset until it emits.
const filter = ref<UsageFilter>(presetRange('30d'));

const option = computed(() =>
  stackedAreaOption(
    dayBuckets.value.map((b) => b.key),
    TOKEN_COMPONENTS.map((component) => ({
      name: component.label,
      values: dayBuckets.value.map(component.get),
    }))
  )
);

const dimSorted = computed(() =>
  [...dimBuckets.value].sort((a, b) => b.total_tokens - a.total_tokens)
);

const dimChart = computed(() =>
  stackedTokenBarOption(
    dimSorted.value.map((b) =>
      dimension.value === 'model'
        ? modelLabel(b.key)
        : b.key || '(unattributed)'
    ),
    dimSorted.value
  )
);

const cachePct = computed(() =>
  overview.value ? (cacheReadShare([overview.value]) * 100).toFixed(1) : '0.0'
);

const span = computed(() => {
  const o = overview.value;
  if (!o || !o.first_event || !o.last_event) return '—';
  const days =
    Math.round(
      (new Date(o.last_event).getTime() - new Date(o.first_event).getTime()) /
        86400000
    ) + 1;
  return `${days} days of history`;
});

async function loadDimension(): Promise<void> {
  dimBuckets.value = (
    await useClient().usage({ groupBy: dimension.value, ...filter.value })
  ).buckets;
}

async function loadAll(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const [days] = await Promise.all([
      client.usage({ groupBy: 'day', ...filter.value }),
      loadDimension(),
    ]);
    dayBuckets.value = days.buckets;
  });
}

async function loadStatic(): Promise<void> {
  const client = useClient();
  overview.value = await client.summaryOverview();
  machines.value = (await client.machines()).map((m) => m.id);
  const all = presetRange('all');
  projects.value = (await client.usage({ groupBy: 'project', ...all })).buckets
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .map((b) => b.key || '(unattributed)');
}

function setDimension(next: 'model' | 'machine' | 'project'): void {
  dimension.value = next;
  void run(loadDimension);
}

function onFilter(next: UsageFilter): void {
  filter.value = next;
  void loadAll();
}

onMounted(() => {
  void loadStatic();
  void loadAll();
});
</script>

<template>
  <AsyncState
    :loading="loading && dayBuckets.length === 0"
    :error="error"
    @retry="retry"
  >
    <section v-if="overview" class="grid tiles">
      <StatTile
        label="All-time tokens"
        :value="formatTokens(overview.total_tokens)"
        :sub="span"
      />
      <StatTile
        label="Cache reads"
        :value="`${cachePct}%`"
        sub="of all tokens"
      />
      <StatTile
        label="Known cost"
        :value="formatCost(overview.cost_usd)"
        sub="priced models, equivalent"
      />
      <StatTile
        label="Sessions"
        :value="String(overview.session_count)"
        :sub="`${overview.machine_count} machine(s)`"
      />
    </section>

    <FilterBar :machines="machines" :projects="projects" @change="onFilter" />

    <section class="card">
      <h3>Daily tokens</h3>
      <EChart :option="option" height="320px" />
      <p class="muted note">Days are bucketed in UTC.</p>
    </section>
    <section class="card">
      <div class="toolbar">
        <h3>By {{ dimension }}</h3>
        <div class="toggle">
          <button
            v-for="d in dims"
            :key="d"
            :class="{ active: dimension === d }"
            @click="setDimension(d)"
          >
            {{ d }}
          </button>
        </div>
      </div>
      <EChart :option="dimChart" height="320px" />
    </section>
  </AsyncState>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
h3 {
  margin: 0 0 1rem;
}
.note {
  font-size: 0.8rem;
  margin: 0.5rem 0 0;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.toggle {
  display: flex;
  gap: 0.25rem;
}
.toggle button {
  font: inherit;
  padding: 0.3rem 0.7rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-secondary);
  cursor: pointer;
}
.toggle button.active {
  background: var(--gridline);
  color: var(--text-primary);
}
</style>
