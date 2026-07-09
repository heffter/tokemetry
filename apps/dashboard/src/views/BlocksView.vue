<script setup lang="ts">
// 5-hour block timeline: token totals, peak per-minute burn, and end
// utilization for each reconstructed block.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import { useClient } from '@/composables/useApi';
import { barOption } from '@/lib/charts';
import { formatPct, formatTokens } from '@/lib/format';
import type { Block } from '@/api/types';

const blocks = ref<Block[]>([]);
const error = ref('');

const chart = computed(() =>
  barOption(
    blocks.value.map((b) => new Date(b.start).toLocaleString()),
    blocks.value.map((b) => b.total_tokens),
    'tokens'
  )
);

async function load(): Promise<void> {
  try {
    blocks.value = await useClient().blocks(720);
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
      <h3>5-hour blocks</h3>
      <EChart :option="chart" height="320px" />
    </section>
    <section class="card">
      <table>
        <thead>
          <tr>
            <th>Start</th>
            <th class="num">Tokens</th>
            <th class="num">Peak/min</th>
            <th class="num">End util</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="block in blocks" :key="block.start">
            <td>{{ new Date(block.start).toLocaleString() }}</td>
            <td class="num tabular">{{ formatTokens(block.total_tokens) }}</td>
            <td class="num tabular">
              {{ formatTokens(block.peak_tokens_per_min) }}
            </td>
            <td class="num tabular">
              {{
                block.end_utilization_pct === null
                  ? '—'
                  : formatPct(block.end_utilization_pct)
              }}
            </td>
          </tr>
        </tbody>
      </table>
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
table {
  width: 100%;
  border-collapse: collapse;
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
</style>
