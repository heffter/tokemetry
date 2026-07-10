<script setup lang="ts">
// 5-hour blocks: the current in-progress block as a hero, then history on a
// real time axis (idle gaps show as gaps) with a range picker and a table.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { timeBarOption } from '@/lib/charts';
import {
  formatCost,
  formatDateTime,
  formatPct,
  formatTokens,
  timeUntil,
  utilizationStatus,
} from '@/lib/format';
import type { Block } from '@/api/types';

const { loading, error, run, retry } = useAsync();
const blocks = ref<Block[]>([]);
const hours = ref(48);
const nowMs = ref(Date.now());
const ranges = [
  { label: '48h', hours: 48 },
  { label: '7d', hours: 168 },
  { label: '30d', hours: 720 },
];

// The block containing "now" is live; otherwise the most recent one is shown
// but flagged as not in progress, so a long-idle block is never called
// "current" at a stale utilization.
const currentInfo = computed<{ block: Block | null; live: boolean }>(() => {
  const inProgress = blocks.value.find(
    (b) =>
      new Date(b.start).getTime() <= nowMs.value &&
      nowMs.value < new Date(b.end).getTime()
  );
  if (inProgress) return { block: inProgress, live: true };
  return { block: blocks.value[blocks.value.length - 1] ?? null, live: false };
});
const current = computed(() => currentInfo.value.block);
const isLive = computed(() => currentInfo.value.live);

const currentStatus = computed(() =>
  isLive.value && current.value?.end_utilization_pct != null
    ? utilizationStatus(current.value.end_utilization_pct)
    : 'good'
);

// Anchor the right edge to "now" so the trailing idle gap since the last block
// is visible rather than the axis stopping at the last bar.
const chart = computed(() =>
  timeBarOption(
    blocks.value.map((b) => [new Date(b.start).getTime(), b.total_tokens]),
    'tokens',
    { axisMax: nowMs.value }
  )
);

// Latest block first: the row you care about should not be buried at the bottom.
const tableBlocks = computed(() => [...blocks.value].reverse());

const rangeLabel = computed(
  () => ranges.find((r) => r.hours === hours.value)?.label ?? ''
);

const totals = computed(() => {
  const priced = blocks.value.filter((b) => b.cost_usd !== null);
  return {
    tokens: blocks.value.reduce((s, b) => s + b.total_tokens, 0),
    peak: Math.max(0, ...blocks.value.map((b) => b.peak_tokens_per_min)),
    cost: priced.length
      ? priced.reduce((s, b) => s + Number(b.cost_usd), 0)
      : null,
  };
});

async function load(): Promise<void> {
  await run(async () => {
    nowMs.value = Date.now();
    blocks.value = await useClient().blocks(hours.value);
  });
}

function setRange(h: number): void {
  hours.value = h;
  void load();
}

onMounted(load);
</script>

<template>
  <AsyncState
    :loading="loading && blocks.length === 0"
    :error="error"
    :empty="!loading && blocks.length === 0"
    :empty-text="`No blocks in the last ${rangeLabel}.`"
    @retry="retry"
  >
    <section v-if="current" class="card hero" :class="currentStatus">
      <div class="muted label">
        {{
          isLive ? 'Current 5-hour block' : 'Last block — no active block now'
        }}
      </div>
      <div class="row">
        <div class="metric">
          <div class="big tabular">
            {{
              current.end_utilization_pct != null
                ? formatPct(current.end_utilization_pct)
                : '—'
            }}
          </div>
          <div class="muted">consumed</div>
        </div>
        <div class="metric">
          <div class="big tabular">
            {{ formatTokens(current.total_tokens) }}
          </div>
          <div class="muted">tokens this block</div>
        </div>
        <div class="metric">
          <div class="big tabular">{{ timeUntil(current.end) }}</div>
          <div class="muted">{{ isLive ? 'until reset' : 'ended' }}</div>
        </div>
        <div class="metric">
          <div class="big tabular">
            {{ formatTokens(current.peak_tokens_per_min) }}/min
          </div>
          <div class="muted">peak burn</div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="head">
        <h3>Block history</h3>
        <div class="toggle">
          <button
            v-for="r in ranges"
            :key="r.hours"
            :class="{ active: hours === r.hours }"
            @click="setRange(r.hours)"
          >
            {{ r.label }}
          </button>
        </div>
      </div>
      <EChart :option="chart" height="300px" />
    </section>

    <section class="card">
      <table>
        <thead>
          <tr>
            <th>Start</th>
            <th class="num">Tokens</th>
            <th class="num">Peak (tok/min)</th>
            <th class="num">Cost</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="block in tableBlocks" :key="block.start">
            <td>{{ formatDateTime(block.start) }}</td>
            <td class="num tabular">{{ formatTokens(block.total_tokens) }}</td>
            <td class="num tabular">
              {{ formatTokens(block.peak_tokens_per_min) }}
            </td>
            <td class="num tabular">{{ formatCost(block.cost_usd) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td>{{ blocks.length }} blocks</td>
            <td class="num tabular">{{ formatTokens(totals.tokens) }}</td>
            <td class="num tabular">{{ formatTokens(totals.peak) }}</td>
            <td class="num tabular">
              {{ totals.cost === null ? '—' : formatCost(String(totals.cost)) }}
            </td>
          </tr>
        </tfoot>
      </table>
    </section>
  </AsyncState>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.hero {
  border-left: 4px solid var(--baseline);
}
.hero.warning {
  border-left-color: var(--status-warning);
}
.hero.critical {
  border-left-color: var(--status-critical);
}
.hero.good {
  border-left-color: var(--status-good);
}
.label {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 0.75rem;
}
.row {
  display: flex;
  gap: 2rem;
  flex-wrap: wrap;
}
.big {
  font-size: 1.6rem;
  font-weight: 600;
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
tfoot td {
  font-weight: 600;
  border-bottom: none;
}
.num {
  text-align: right;
}
.util.warning {
  color: var(--status-warning);
  font-weight: 600;
}
.util.critical {
  color: var(--status-critical);
  font-weight: 600;
}
</style>
