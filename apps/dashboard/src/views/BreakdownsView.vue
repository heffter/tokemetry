<script setup lang="ts">
// Breakdowns across model, machine, and project as stacked token composition,
// plus honest cache metrics.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import ChartTable from '@/components/ChartTable.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import StatTile from '@/components/StatTile.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  calendarOption,
  punchCardOption,
  stackedTokenBarOption,
  tokenTableRows,
  TOKEN_TABLE_HEADERS,
} from '@/lib/charts';
import {
  cacheReadShare,
  formatCost,
  formatPct,
  modelLabel,
} from '@/lib/format';
import { cacheSavingsUsd } from '@/lib/coverage';
import { presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type { HeatmapResponse, PricingRow, UsageBucket } from '@/api/types';

const dimLabel = (b: UsageBucket): string => b.key || '(unattributed)';

const { loading, error, run, retry } = useAsync();
const byModel = ref<UsageBucket[]>([]);
const byMachine = ref<UsageBucket[]>([]);
const byProject = ref<UsageBucket[]>([]);
const heatmap = ref<HeatmapResponse | null>(null);
const machines = ref<string[]>([]);
const pricing = ref<PricingRow[]>([]);
const filter = ref<UsageFilter>(presetRange('30d'));
// Absolute magnitude vs normalized composition (100% bars); the latter keeps
// small components legible under cache-read dominance.
const mode = ref<'absolute' | 'percent'>('absolute');
const stackOpts = computed(() => ({ normalized: mode.value === 'percent' }));

const cacheSavings = computed(() =>
  cacheSavingsUsd(byModel.value, pricing.value)
);

const punchChart = computed(() =>
  punchCardOption(heatmap.value?.punch_card ?? [])
);
const calendarChart = computed(() =>
  calendarOption(heatmap.value?.calendar ?? [])
);

/** Sort descending by total tokens so the biggest driver reads leftmost. */
function sorted(buckets: UsageBucket[]): UsageBucket[] {
  return [...buckets].sort((a, b) => b.total_tokens - a.total_tokens);
}

const modelChart = computed(() =>
  stackedTokenBarOption(
    sorted(byModel.value).map((b) => modelLabel(b.key)),
    sorted(byModel.value),
    stackOpts.value
  )
);
const machineChart = computed(() =>
  stackedTokenBarOption(
    sorted(byMachine.value).map((b) => b.key || '(unattributed)'),
    sorted(byMachine.value),
    stackOpts.value
  )
);
const projectChart = computed(() =>
  stackedTokenBarOption(
    sorted(byProject.value).map((b) => b.key || '(unattributed)'),
    sorted(byProject.value),
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
    const [model, machine, project, heat] = await Promise.all([
      client.usage({ groupBy: 'model', ...f }),
      client.usage({ groupBy: 'machine', ...f }),
      client.usage({ groupBy: 'project', ...f }),
      client.heatmap(f.from, f.to, f.machine, f.project),
    ]);
    byModel.value = model.buckets;
    byMachine.value = machine.buckets;
    byProject.value = project.buckets;
    heatmap.value = heat;
  });
}

async function loadOptions(): Promise<void> {
  try {
    const client = useClient();
    machines.value = (await client.machines()).map((m) => m.id);
    pricing.value = await client.pricing();
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
      <FilterBar :machines="machines" :projects="projects" @change="onFilter" />
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
          A high cache-read share (often ~95%) is normal, not waste — Claude
          Code re-reads the cached system prompt and conversation on every turn,
          and cache reads are billed at a fraction of fresh input tokens.
        </p>
      </section>
      <section class="card">
        <h3>By model</h3>
        <EChart :option="modelChart" height="300px" />
        <ChartTable
          caption="Tokens by model and token type"
          :columns="['Model', ...TOKEN_TABLE_HEADERS]"
          :rows="tokenTableRows(sorted(byModel), (b) => modelLabel(b.key))"
        />
      </section>
      <section class="card">
        <h3>By machine</h3>
        <EChart :option="machineChart" height="300px" />
        <ChartTable
          caption="Tokens by machine and token type"
          :columns="['Machine', ...TOKEN_TABLE_HEADERS]"
          :rows="tokenTableRows(sorted(byMachine), dimLabel)"
        />
      </section>
      <section class="card">
        <h3>By project</h3>
        <EChart :option="projectChart" height="300px" />
        <ChartTable
          caption="Tokens by project and token type"
          :columns="['Project', ...TOKEN_TABLE_HEADERS]"
          :rows="tokenTableRows(sorted(byProject), dimLabel)"
        />
      </section>
      <section class="card">
        <h3>When you burn tokens (weekday × hour)</h3>
        <EChart :option="punchChart" height="260px" />
      </section>
      <section class="card">
        <h3>Daily activity</h3>
        <EChart :option="calendarChart" height="200px" />
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
</style>
