<script setup lang="ts">
// Daily usage trend as a real stacked area over the five token components,
// plus a categorical breakdown by a chosen dimension (stacked composition).
import { computed, onMounted, ref, watch } from 'vue';
import EChart from '@/components/EChart.vue';
import { useClient } from '@/composables/useApi';
import {
  stackedAreaOption,
  stackedTokenBarOption,
  TOKEN_COMPONENTS,
} from '@/lib/charts';
import { modelLabel } from '@/lib/format';
import type { UsageBucket } from '@/api/types';

const dimension = ref<'model' | 'machine' | 'project'>('model');
const dims = ['model', 'machine', 'project'] as const;
const dayBuckets = ref<UsageBucket[]>([]);
const dimBuckets = ref<UsageBucket[]>([]);
const error = ref('');

// Daily chart: a genuine stack of the five token components over time, so the
// cache-read share is visible day by day instead of one opaque total line.
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

async function loadDays(): Promise<void> {
  try {
    dayBuckets.value = (await useClient().usage({ groupBy: 'day' })).buckets;
  } catch (e) {
    error.value = String(e);
  }
}

async function loadDimension(): Promise<void> {
  try {
    dimBuckets.value = (
      await useClient().usage({ groupBy: dimension.value })
    ).buckets;
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(() => {
  void loadDays();
  void loadDimension();
});
// Only the dimension chart depends on the toggle; the daily chart does not.
watch(dimension, loadDimension);
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
