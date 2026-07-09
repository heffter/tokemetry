<script setup lang="ts">
// A single limit-window gauge: a horizontal meter with a status color, the
// utilization percentage, provenance, and time-to-reset. Status is carried by
// color plus the visible percentage label (never color alone).
import { computed } from 'vue';
import {
  formatPct,
  timeUntil,
  utilizationStatus,
  windowLabel,
} from '@/lib/format';
import type { Limit } from '@/api/types';

const props = defineProps<{ limit: Limit }>();

const status = computed(() => utilizationStatus(props.limit.utilization_pct));
const color = computed(() => `var(--status-${status.value})`);
const width = computed(() => `${Math.min(100, props.limit.utilization_pct)}%`);
</script>

<template>
  <div class="card gauge">
    <div class="head">
      <span>{{ windowLabel(limit.window_kind) }}</span>
      <span class="tabular pct" :style="{ color }">{{
        formatPct(limit.utilization_pct)
      }}</span>
    </div>
    <div class="track">
      <div class="fill" :style="{ width, background: color }"></div>
    </div>
    <div class="foot muted">
      <span>{{ limit.provenance }}</span>
      <span>resets {{ timeUntil(limit.resets_at) }}</span>
    </div>
  </div>
</template>

<style scoped>
.gauge {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.head {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
}
.pct {
  font-size: 1.1rem;
}
.track {
  height: 10px;
  border-radius: 6px;
  background: var(--gridline);
  overflow: hidden;
}
.fill {
  height: 100%;
  border-radius: 6px;
  transition: width 0.4s ease;
}
.foot {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
}
</style>
