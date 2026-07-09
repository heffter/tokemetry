<script setup lang="ts">
// Breakdowns across model, machine, and project, plus cache efficiency.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import { useClient } from '@/composables/useApi';
import { barOption } from '@/lib/charts';
import { formatTokens } from '@/lib/format';
import type { UsageBucket } from '@/api/types';

const byModel = ref<UsageBucket[]>([]);
const byMachine = ref<UsageBucket[]>([]);
const byProject = ref<UsageBucket[]>([]);
const error = ref('');

const modelChart = computed(() => chart(byModel.value));
const machineChart = computed(() => chart(byMachine.value));
const projectChart = computed(() => chart(byProject.value));

const cache = computed(() => {
  const total = byModel.value.reduce(
    (acc, b) => {
      acc.read += b.cache_read_tokens;
      acc.input += b.input_tokens;
      acc.write += b.cache_write_short_tokens + b.cache_write_long_tokens;
      return acc;
    },
    { read: 0, input: 0, write: 0 }
  );
  const denom = total.read + total.input + total.write;
  const hitRatio = denom === 0 ? 0 : (100 * total.read) / denom;
  return { ...total, hitRatio };
});

function chart(buckets: UsageBucket[]) {
  return barOption(
    buckets.map((b) => b.key || '(none)'),
    buckets.map((b) => b.total_tokens),
    'tokens'
  );
}

async function load(): Promise<void> {
  try {
    const client = useClient();
    byModel.value = (await client.usage({ groupBy: 'model' })).buckets;
    byMachine.value = (await client.usage({ groupBy: 'machine' })).buckets;
    byProject.value = (await client.usage({ groupBy: 'project' })).buckets;
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
      <h3>Cache efficiency</h3>
      <p>
        Cache-read tokens:
        <strong class="tabular">{{ formatTokens(cache.read) }}</strong> —
        <strong class="tabular">{{ cache.hitRatio.toFixed(1) }}%</strong> of
        input volume served from cache.
      </p>
    </section>
    <section class="card">
      <h3>By model</h3>
      <EChart :option="modelChart" height="280px" />
    </section>
    <section class="card">
      <h3>By machine</h3>
      <EChart :option="machineChart" height="280px" />
    </section>
    <section class="card">
      <h3>By project</h3>
      <EChart :option="projectChart" height="280px" />
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
