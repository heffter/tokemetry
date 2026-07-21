<script setup lang="ts">
// Breakdowns across model, machine, and project as stacked token composition
// (including reasoning), plus honest cache metrics. The composition is served
// from /api/v2/rollups -- fetched once for the range and aggregated
// client-side three ways -- so it is provider-neutral and unbounded; the
// weekday/hour heatmap still comes from the v1 endpoint (which has no provider
// param yet, tracked for a v2 heatmap).
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import ChartTable from '@/components/ChartTable.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import StatTile from '@/components/StatTile.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { useGlobalFilters } from '@/composables/useGlobalFilters';
import {
  barOption,
  calendarOption,
  componentTableRows,
  punchCardOption,
  stackedComponentBarOption,
  TOKEN_TABLE_HEADERS_V2,
  V2_TOKEN_COMPONENTS,
} from '@/lib/charts';
import { cacheReadShare, formatCost, formatPct } from '@/lib/format';
import { aggregateRollups, ROLLUP_DIMENSIONS } from '@/lib/rollups';
import { knownModelIds, resolveModel } from '@/lib/modelRegistry';
import { v2CalendarBuckets, v2PunchCells } from '@/lib/heatmapV2';
import { loadSelection, saveSelection } from '@/composables/useSettings';
import { clampRangeDays, presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';

// The v2 heatmap and cache-savings endpoints reject a span wider than the
// server's 366-day maximum, so the "All" preset must be clamped or it 400s.
const MAX_RANGE_DAYS = 365;
import type { HeatmapV2Response } from '@/api/client';
import type { ModelV2, ProviderV2, RollupV2, UsageRowV2 } from '@/api/types-v2';

const dimLabel = (b: UsageRowV2): string => b.key || '(unattributed)';

const { loading, error, run, retry } = useAsync();
const { provider: globalProvider } = useGlobalFilters();
const rollupRows = ref<RollupV2[]>([]);
const heatmap = ref<HeatmapV2Response | null>(null);
const cacheSavings = ref<number>(0);
const machines = ref<string[]>([]);
const providers = ref<ProviderV2[]>([]);
const models = ref<ModelV2[]>([]);
const filter = ref<UsageFilter>(presetRange('30d'));
// Set when a long selection (e.g. "All") is clamped to the most recent window.
const clampedFrom = ref<string | null>(null);
// Absolute magnitude vs normalized composition (100% bars); the latter keeps
// small components legible under cache-read dominance.
const mode = ref<'absolute' | 'percent'>('absolute');
// Cache-read deselected by default; hiding it re-normalizes the rest to 100%.
const selection = ref(loadSelection('breakdowns', { 'cache read': false }));
function onLegend(sel: Record<string, boolean>): void {
  selection.value = sel;
  saveSelection('breakdowns', sel);
}
const stackOpts = computed(() => ({
  normalized: mode.value === 'percent',
  selected: selection.value,
}));

const knownIds = computed(() => knownModelIds(models.value));
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

// The rollups endpoint has no project param, so project is scoped client-side.
const scopedRows = computed(() =>
  filter.value.project
    ? rollupRows.value.filter((r) => r.project === filter.value.project)
    : rollupRows.value
);
const byModel = computed(() =>
  aggregateRollups(scopedRows.value, ROLLUP_DIMENSIONS.model)
);
const byMachine = computed(() =>
  aggregateRollups(scopedRows.value, ROLLUP_DIMENSIONS.machine)
);
const byProject = computed(() =>
  aggregateRollups(scopedRows.value, ROLLUP_DIMENSIONS.project)
);

const punchChart = computed(() =>
  punchCardOption(heatmap.value ? v2PunchCells(heatmap.value) : [])
);
const calendarChart = computed(() =>
  calendarOption(heatmap.value ? v2CalendarBuckets(heatmap.value) : [])
);

// Tokens by hour of day (0-23), summed across every weekday in the range, so
// the shape of a working day is visible at a glance -- when the heavy hours are.
const hourlyChart = computed(() => {
  const byHour = new Array(24).fill(0) as number[];
  for (const cell of heatmap.value?.punch_card ?? []) {
    byHour[cell.hour] += cell.value;
  }
  return barOption(
    byHour.map((_, hour) => String(hour).padStart(2, '0')),
    byHour,
    'tokens'
  );
});
const hasHourly = computed(() =>
  (heatmap.value?.punch_card ?? []).some((c) => c.value > 0)
);

/** Sort descending by total tokens so the biggest driver reads leftmost. */
function sorted(rows: UsageRowV2[]): UsageRowV2[] {
  return [...rows].sort((a, b) => b.total_tokens - a.total_tokens);
}

const modelChart = computed(() =>
  stackedComponentBarOption(
    sorted(byModel.value).map(
      (b) => resolveModel(b.key, knownIds.value).display
    ),
    sorted(byModel.value),
    V2_TOKEN_COMPONENTS,
    stackOpts.value
  )
);
const machineChart = computed(() =>
  stackedComponentBarOption(
    sorted(byMachine.value).map((b) => b.key || '(unattributed)'),
    sorted(byMachine.value),
    V2_TOKEN_COMPONENTS,
    stackOpts.value
  )
);
const projectChart = computed(() =>
  stackedComponentBarOption(
    sorted(byProject.value).map((b) => b.key || '(unattributed)'),
    sorted(byProject.value),
    V2_TOKEN_COMPONENTS,
    stackOpts.value
  )
);

// Two well-defined, correctly-labelled cache metrics (the old single ratio
// contradicted its own denominator).
const cacheStats = computed(() => {
  let read = 0;
  let promptInput = 0;
  let write = 0;
  for (const b of byModel.value) {
    read += b.cache_read_tokens;
    promptInput += b.input_tokens;
    write += b.cache_write_short_tokens + b.cache_write_long_tokens;
  }
  const prompt = read + promptInput;
  const hitRatio = prompt === 0 ? 0 : read / prompt;
  const reuse = write === 0 ? 0 : read / write;
  return { read, hitRatio, reuse, share: cacheReadShare(byModel.value) };
});

const projects = ref<string[]>([]);

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const f = filter.value;
    const fallback = presetRange('30d');
    // Clamp the span so the bounded heatmap/cache-savings endpoints accept it
    // (the "All" preset spans years and would 400); flag when it happens.
    const clamped = clampRangeDays(
      f.from ?? fallback.from,
      f.to ?? fallback.to,
      MAX_RANGE_DAYS
    );
    clampedFrom.value = clamped.clamped ? clamped.from : null;
    const from = clamped.from;
    const to = clamped.to;
    // The v2 heatmap and cache-savings endpoints honor the global provider
    // filter server-side (Task 74), so BreakdownsView's punch card, calendar,
    // and "Caching saved" tile obey it just like the rollup charts.
    const v2Filters = {
      from,
      to,
      provider: globalProvider.value || f.provider,
      model: f.model,
      machine: f.machine,
      project: f.project,
    };
    const [rollups, heat, saved] = await Promise.all([
      client.v2AllRollups({
        from,
        to,
        provider: f.provider,
        model: f.model,
        machine: f.machine,
      }),
      client.heatmapV2(v2Filters),
      client.cacheSavings(v2Filters),
    ]);
    rollupRows.value = rollups;
    heatmap.value = heat;
    cacheSavings.value = Number(saved.cache_savings_usd);
  });
}

async function loadOptions(): Promise<void> {
  try {
    const client = useClient();
    machines.value = (await client.machines()).map((m) => m.id);
    providers.value = await client.v2Providers();
    models.value = await client.v2Models();
    const all = presetRange('all');
    projects.value = (
      await client.usage({ groupBy: 'project', ...all })
    ).buckets
      // Drop the empty (unattributed/bootstrap) key: not filterable.
      .filter((b) => b.key)
      .sort((a, b) => b.total_tokens - a.total_tokens)
      .map((b) => b.key);
  } catch {
    projects.value = [];
  }
}

function onFilter(next: UsageFilter): void {
  filter.value = next;
  void load();
}

onMounted(() => {
  void loadOptions();
  void load();
});
</script>

<template>
  <div>
    <div class="controls">
      <FilterBar
        :providers="providerOptions"
        :models="modelOptions"
        :machines="machines"
        :projects="projects"
        @change="onFilter"
      />
      <div class="toggle">
        <button
          :class="{ active: mode === 'absolute' }"
          @click="mode = 'absolute'"
        >
          Absolute
        </button>
        <button
          :class="{ active: mode === 'percent' }"
          @click="mode = 'percent'"
        >
          Composition %
        </button>
      </div>
    </div>

    <p v-if="clampedFrom" class="muted note clamp">
      Range clamped to the most recent {{ MAX_RANGE_DAYS }} days (from
      {{ clampedFrom }}) — the heatmap and cache metrics are bounded.
    </p>

    <AsyncState
      :loading="loading && byModel.length === 0"
      :error="error"
      :empty="!loading && byModel.length === 0 && byProject.length === 0"
      empty-text="No usage in this range."
      @retry="retry"
    >
      <section class="card">
        <h3>Cache</h3>
        <div class="grid tiles">
          <StatTile
            label="Cache-read share"
            :value="formatPct(cacheStats.share * 100)"
            sub="of all tokens"
          />
          <StatTile
            label="Served from cache"
            :value="formatPct(cacheStats.hitRatio * 100)"
            sub="of prompt tokens"
          />
          <StatTile
            label="Cache reuse"
            :value="`${cacheStats.reuse.toFixed(1)}x`"
            sub="reads per cached token"
          />
          <StatTile
            v-if="cacheSavings > 0"
            label="Caching saved"
            :value="formatCost(String(cacheSavings))"
            sub="vs full input price"
          />
        </div>
        <p class="muted note">
          A high cache-read share (often ~95%) is normal, not waste — coding
          agents re-read the cached system prompt and conversation on every
          turn, and cache reads are billed at a fraction of fresh input tokens.
        </p>
      </section>
      <section class="card">
        <h3>By model</h3>
        <EChart :option="modelChart" height="300px" @legend-select="onLegend" />
        <ChartTable
          caption="Tokens by model and token type"
          :columns="['Model', ...TOKEN_TABLE_HEADERS_V2]"
          :rows="
            componentTableRows(
              sorted(byModel),
              V2_TOKEN_COMPONENTS,
              (b) => resolveModel(b.key, knownIds).display
            )
          "
        />
      </section>
      <section class="card">
        <h3>By machine</h3>
        <EChart
          :option="machineChart"
          height="300px"
          @legend-select="onLegend"
        />
        <ChartTable
          caption="Tokens by machine and token type"
          :columns="['Machine', ...TOKEN_TABLE_HEADERS_V2]"
          :rows="
            componentTableRows(sorted(byMachine), V2_TOKEN_COMPONENTS, dimLabel)
          "
        />
      </section>
      <section class="card">
        <h3>By project</h3>
        <EChart
          :option="projectChart"
          height="300px"
          @legend-select="onLegend"
        />
        <ChartTable
          caption="Tokens by project and token type"
          :columns="['Project', ...TOKEN_TABLE_HEADERS_V2]"
          :rows="
            componentTableRows(sorted(byProject), V2_TOKEN_COMPONENTS, dimLabel)
          "
        />
      </section>
      <section v-if="hasHourly" class="card">
        <h3>Tokens by hour of day</h3>
        <EChart :option="hourlyChart" height="240px" />
        <p class="muted note">
          Total tokens per hour (UTC), summed across every day in the range —
          the shape of your working day.
        </p>
      </section>
      <section class="card">
        <h3>When you burn tokens (weekday × hour)</h3>
        <div class="chart-scroll">
          <EChart :option="punchChart" height="260px" />
        </div>
      </section>
      <section class="card">
        <h3>Daily activity</h3>
        <div class="chart-scroll">
          <EChart :option="calendarChart" height="200px" />
        </div>
      </section>
    </AsyncState>
  </div>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0 0 1rem;
}
.controls {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin-bottom: 0.75rem;
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
.note {
  font-size: 0.85rem;
  margin-bottom: 0;
}
.clamp {
  margin: 0 0 1rem;
}

.chart-scroll {
  overflow-x: auto;
  overscroll-behavior-x: contain;
  padding-bottom: 0.35rem;
}

@media (max-width: 760px) {
  .chart-scroll {
    margin-inline: -0.15rem;
  }

  .chart-scroll :deep(.echart) {
    min-width: 500px !important;
  }
}
</style>
