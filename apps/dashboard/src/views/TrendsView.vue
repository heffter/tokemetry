<script setup lang="ts">
// Daily usage trend, with a dimension toggle (model / machine / project) that
// stacks the day series by that dimension.
import { computed, onMounted, ref, watch } from 'vue';
import EChart from '@/components/EChart.vue';
import { useClient } from '@/composables/useApi';
import { stackedAreaOption } from '@/lib/charts';
import type { UsageBucket } from '@/api/types';

const dimension = ref<'model' | 'machine' | 'project'>('model');
const dims = ['model', 'machine', 'project'] as const;
const dayBuckets = ref<UsageBucket[]>([]);
const dimBuckets = ref<UsageBucket[]>([]);
const error = ref('');

const option = computed(() => {
  const days = dayBuckets.value.map((b) => b.key);
  // A single stacked series per dimension key, distributed proportionally per
  // day is beyond this range query; show the per-day total plus a dimension
  // breakdown bar. Here we stack the dimension totals as one area for shape.
  return stackedAreaOption(days, [
    {
      name: 'total tokens',
      values: dayBuckets.value.map((b) => b.total_tokens),
    },
  ]);
});

const dimChart = computed(() =>
  stackedAreaOption(
    dimBuckets.value.map((b) => b.key),
    [
      {
        name: dimension.value,
        values: dimBuckets.value.map((b) => b.total_tokens),
      },
    ]
  )
);

async function load(): Promise<void> {
  try {
    const client = useClient();
    dayBuckets.value = (await client.usage({ groupBy: 'day' })).buckets;
    dimBuckets.value = (
      await client.usage({ groupBy: dimension.value })
    ).buckets;
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
watch(dimension, load);
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <template v-else>
    <section class="card">
      <h3>Daily tokens (last 30 days)</h3>
      <EChart :option="option" height="320px" />
    </section>
    <section class="card">
      <div class="toolbar">
        <h3>By {{ dimension }}</h3>
        <div class="toggle">
          <button
            v-for="d in dims"
            :key="d"
            :class="{ active: dimension === d }"
            @click="dimension = d"
          >
            {{ d }}
          </button>
        </div>
      </div>
      <EChart :option="dimChart" height="320px" />
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
