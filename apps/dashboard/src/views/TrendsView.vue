<script setup lang="ts">
// Daily usage trend as a real stacked area over the v2 token components
// (including reasoning), plus a categorical breakdown by a chosen dimension.
// Both charts are served from /api/v2/rollups: the rollup rows are fetched once
// for the range and aggregated client-side by day and by the chosen dimension,
// so full history works without hitting the v2 /usage range bound. An all-time
// summary strip sits on top; a shared FilterBar drives the date range plus the
// global provider/model filter and the machine/project scope.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import StatTile from '@/components/StatTile.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { useGlobalFilters } from '@/composables/useGlobalFilters';
import {
  stackedAreaOption,
  stackedComponentBarOption,
  V2_TOKEN_COMPONENTS,
} from '@/lib/charts';
import { cacheReadShare, formatCost, formatTokens } from '@/lib/format';
import { aggregateRollups, ROLLUP_DIMENSIONS } from '@/lib/rollups';
import { knownModelIds, resolveModel } from '@/lib/modelRegistry';
import { enumerateDays, presetRange } from '@/lib/filters';
import { loadSelection, saveSelection } from '@/composables/useSettings';
import type { UsageFilter } from '@/lib/filters';
import type { Overview } from '@/api/types';
import type { ModelV2, ProviderV2, RollupV2, UsageRowV2 } from '@/api/types-v2';

type TrendDimension =
  'model' | 'machine' | 'project' | 'source' | 'environment';
const dims: TrendDimension[] = [
  'model',
  'machine',
  'project',
  'source',
  'environment',
];

/** An empty day bucket used to gap-fill days with no usage. */
function zeroRow(key: string): UsageRowV2 {
  return {
    key,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_short_tokens: 0,
    cache_write_long_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    attempt_count: 0,
  };
}

/** "2026-06-01" -> "Jun 1" (UTC), for a compact day axis label. */
function shortDay(key: string): string {
  const date = new Date(`${key}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? key
    : date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
      });
}

const { loading, error, run, retry } = useAsync();
const { provider: globalProvider } = useGlobalFilters();
const dimension = ref<TrendDimension>('model');
// Raw rollup rows for the current range; both charts derive from these.
const rollupRows = ref<RollupV2[]>([]);
const overview = ref<Overview | null>(null);
const machines = ref<string[]>([]);
const projects = ref<string[]>([]);
const providers = ref<ProviderV2[]>([]);
const models = ref<ModelV2[]>([]);
// Default matches the FilterBar's initial 30d preset until it emits.
const filter = ref<UsageFilter>(presetRange('30d'));

const knownIds = computed(() => knownModelIds(models.value));
const providerOptions = computed(() =>
  providers.value.map((p) => ({ value: p.id, label: p.display_name }))
);
// Models are scoped to the selected provider when one is chosen.
const modelOptions = computed(() =>
  models.value
    .filter((m) => !globalProvider.value || m.provider === globalProvider.value)
    .map((m) => ({
      value: m.native_model_id,
      label: resolveModel(m.native_model_id, knownIds.value).display,
    }))
);

// The rollups endpoint has no project filter param, so project is applied
// client-side over the fetched rows.
const scopedRows = computed(() =>
  filter.value.project
    ? rollupRows.value.filter((r) => r.project === filter.value.project)
    : rollupRows.value
);

const dayBuckets = computed(() =>
  aggregateRollups(scopedRows.value, ROLLUP_DIMENSIONS.day)
);
const dimBuckets = computed(() =>
  aggregateRollups(scopedRows.value, ROLLUP_DIMENSIONS[dimension.value])
);

// Cache-read is deselected by default across both composition charts (it
// dominates and misleads); the selection survives auto-refresh and reloads.
const selection = ref(loadSelection('trends', { 'cache read': false }));
function onLegend(sel: Record<string, boolean>): void {
  selection.value = sel;
  saveSelection('trends', sel);
}

// Gap-fill the daily series over its own span so days with no usage render as
// zero instead of being silently collapsed out of the axis.
const option = computed(() => {
  const buckets = dayBuckets.value;
  if (buckets.length === 0) return stackedAreaOption([], []);
  const byKey = new Map(buckets.map((b) => [b.key, b]));
  const keys = [...byKey.keys()].sort();
  const days = enumerateDays(keys[0], keys[keys.length - 1]);
  const filled = days.map((d) => byKey.get(d) ?? zeroRow(d));
  return stackedAreaOption(
    days.map(shortDay),
    V2_TOKEN_COMPONENTS.map((component) => ({
      name: component.label,
      values: filled.map(component.get),
    })),
    { selected: selection.value }
  );
});

const dimSorted = computed(() =>
  [...dimBuckets.value].sort((a, b) => b.total_tokens - a.total_tokens)
);

// Normalized so composition is comparable across categories despite the ~1000x
// magnitude spread and cache-read dominance.
const dimChart = computed(() =>
  stackedComponentBarOption(
    dimSorted.value.map((b) =>
      dimension.value === 'model'
        ? resolveModel(b.key, knownIds.value).display
        : b.key || '(unattributed)'
    ),
    dimSorted.value,
    V2_TOKEN_COMPONENTS,
    { normalized: true, selected: selection.value }
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

async function loadAll(): Promise<void> {
  await run(async () => {
    const fallback = presetRange('30d');
    rollupRows.value = await useClient().v2AllRollups({
      from: filter.value.from ?? fallback.from,
      to: filter.value.to ?? fallback.to,
      provider: filter.value.provider,
      model: filter.value.model,
      machine: filter.value.machine,
    });
  });
}

async function loadStatic(): Promise<void> {
  // Filter options and the summary strip are non-critical chrome; a failure
  // here must not blank the page, so swallow and leave them empty.
  try {
    const client = useClient();
    overview.value = await client.summaryOverview();
    machines.value = (await client.machines()).map((m) => m.id);
    providers.value = await client.v2Providers();
    models.value = await client.v2Models();
    const all = presetRange('all');
    projects.value = (
      await client.usage({ groupBy: 'project', ...all })
    ).buckets
      // Drop the empty (unattributed/bootstrap) key: it has no project to
      // filter by, and sending its label as the value matched nothing.
      .filter((b) => b.key)
      .sort((a, b) => b.total_tokens - a.total_tokens)
      .map((b) => b.key);
  } catch {
    projects.value = [];
  }
}

// The breakdown re-aggregates the already-fetched rollups client-side, so
// switching dimension needs no refetch.
function setDimension(next: TrendDimension): void {
  dimension.value = next;
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
  <div>
    <!-- Summary strip and filters live outside AsyncState so a chart-load
         error never removes the controls the user needs to recover. -->
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

    <FilterBar
      :providers="providerOptions"
      :models="modelOptions"
      :machines="machines"
      :projects="projects"
      @change="onFilter"
    />

    <AsyncState
      :loading="loading && dayBuckets.length === 0"
      :error="error"
      @retry="retry"
    >
      <section class="card">
        <h3>Daily tokens</h3>
        <EChart :option="option" height="320px" @legend-select="onLegend" />
        <p class="muted note">Days are bucketed in UTC; gaps show as zero.</p>
      </section>
      <section class="card">
        <div class="toolbar">
          <h3>By {{ dimension }} (composition)</h3>
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
        <EChart :option="dimChart" height="320px" @legend-select="onLegend" />
      </section>
    </AsyncState>
  </div>
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
