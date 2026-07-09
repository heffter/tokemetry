<script setup lang="ts">
// Breakdowns across model, machine, and project as stacked token composition,
// plus honest cache metrics.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import { useClient } from '@/composables/useApi';
import { stackedTokenBarOption } from '@/lib/charts';
import {
  cacheReadShare,
  formatPct,
  formatTokens,
  modelLabel,
} from '@/lib/format';
import type { UsageBucket } from '@/api/types';

const byModel = ref<UsageBucket[]>([]);
const byMachine = ref<UsageBucket[]>([]);
const byProject = ref<UsageBucket[]>([]);
const error = ref('');

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

async function load(): Promise<void> {
  try {
    const client = useClient();
    const [model, machine, project] = await Promise.all([
      client.usage({ groupBy: 'model' }),
      client.usage({ groupBy: 'machine' }),
      client.usage({ groupBy: 'project' }),
    ]);
    byModel.value = model.buckets;
    byMachine.value = machine.buckets;
    byProject.value = project.buckets;
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <template v-else>
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
    </section>
    <section class="card">
      <h3>By machine</h3>
      <EChart :option="machineChart" height="300px" />
    </section>
    <section class="card">
      <h3>By project</h3>
      <EChart :option="projectChart" height="300px" />
    </section>
  </template>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0 0 1rem;
}
</style>
