<script setup lang="ts">
// Breakdowns across model, machine, and project as stacked token composition,
// plus honest cache metrics.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import ChartTable from '@/components/ChartTable.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
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
  formatPct,
  formatTokens,
  modelLabel,
} from '@/lib/format';
import { presetRange } from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type { HeatmapResponse, UsageBucket } from '@/api/types';

const dimLabel = (b: UsageBucket): string => b.key || '(unattributed)';

const { loading, error, run, retry } = useAsync();
const byModel = ref<UsageBucket[]>([]);
const byMachine = ref<UsageBucket[]>([]);
const byProject = ref<UsageBucket[]>([]);
const heatmap = ref<HeatmapResponse | null>(null);
const machines = ref<string[]>([]);
const filter = ref<UsageFilter>(presetRange('30d'));

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
    sorted(byModel.value)
  )
);
const machineChart = computed(() =>
  stackedTokenBarOption(
    sorted(byMachine.value).map((b) => b.key || '(unattributed)'),
    sorted(byMachine.value)
  )
);
const projectChart = computed(() =>
  stackedTokenBarOption(
    sorted(byProject.value).map((b) => b.key || '(unattributed)'),
    sorted(byProject.value)
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
      client.heatmap(f.from, f.to),
    ]);
    byModel.value = model.buckets;
    byMachine.value = machine.buckets;
    byProject.value = project.buckets;
    heatmap.value = heat;
  });
}

async function loadOptions(): Promise<void> {
  const client = useClient();
  machines.value = (await client.machines()).map((m) => m.id);
  const all = presetRange('all');
  projects.value = (await client.usage({ groupBy: 'project', ...all })).buckets
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .map((b) => b.key || '(unattributed)');
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
  <AsyncState
    :loading="loading && byModel.length === 0"
    :error="error"
    @retry="retry"
  >
    <FilterBar :machines="machines" :projects="projects" @change="onFilter" />
    <section class="card">
      <h3>Cache</h3>
      <p>
        <strong class="tabular">{{ formatTokens(cacheStats.read) }}</strong>
        cache-read tokens —
        <strong class="tabular">{{
          formatPct(cacheStats.hitRatio * 100)
        }}</strong>
        of prompt tokens served from cache, each cached token reused
        <strong class="tabular">{{ cacheStats.reuse.toFixed(1) }}x</strong> on
        average.
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
      <EChart :option="calendarChart" height="180px" />
    </section>
  </AsyncState>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0 0 1rem;
}
</style>
