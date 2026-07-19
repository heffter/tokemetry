<script setup lang="ts">
// A single limit-window gauge: a horizontal meter with a status color, the
// utilization percentage, provenance, and time-to-reset. Status is carried by
// color plus the visible percentage label (never color alone).
import { computed } from 'vue';
import Sparkline from '@/components/Sparkline.vue';
import {
  formatDateTime,
  formatDuration,
  formatPct,
  isLongHorizonReset,
  timeUntil,
  utilizationStatus,
  windowLabel,
} from '@/lib/format';
import type { Limit } from '@/api/types';

const props = withDefaults(
  defineProps<{
    limit: Limit;
    history?: number[];
    projected?: number | null;
    /** Registry-derived window labels; falls back to the Anthropic seed. */
    windowLabels?: Record<string, string>;
  }>(),
  { history: () => [], projected: null, windowLabels: undefined }
);

// A snapshot older than this is flagged: the collector has not reported
// recently so the reading (and its reset) may be behind the live window.
const STALE_SECONDS = 600;

const status = computed(() => utilizationStatus(props.limit.utilization_pct));
const color = computed(() => `var(--status-${status.value})`);
const width = computed(() => `${Math.min(100, props.limit.utilization_pct)}%`);

// A far-out reset (any provider's long window, not just Anthropic's weekly)
// reads better as an absolute date than a long countdown; a near reset keeps
// the countdown primary. Derived from the reset time, not the window name.
const longHorizon = computed(() => isLongHorizonReset(props.limit.resets_at));
const isStale = computed(() => props.limit.age_seconds >= STALE_SECONDS);

const resetText = computed(() => {
  const resets = props.limit.resets_at;
  if (resets === null) return 'resets —';
  return longHorizon.value
    ? `resets ${formatDateTime(resets)}`
    : `resets ${timeUntil(resets)}`;
});

const resetTitle = computed(() => {
  const resets = props.limit.resets_at;
  if (resets === null) return '';
  const complement = longHorizon.value
    ? timeUntil(resets)
    : formatDateTime(resets);
  return props.limit.derived_reset
    ? `${complement} · estimated from a ${formatDuration(props.limit.age_seconds)}-old snapshot`
    : complement;
});
</script>

<template>
  <div class="card gauge">
    <div class="head">
      <span>{{ windowLabel(limit.window_kind, windowLabels) }}</span>
      <span class="tabular pct" :style="{ color }">{{
        formatPct(limit.utilization_pct)
      }}</span>
    </div>
    <div class="track">
      <div class="fill" :style="{ width, background: color }"></div>
    </div>
    <div v-if="history.length >= 4" class="trend">
      <span class="muted trend-label">24h trend</span>
      <Sparkline
        :values="history"
        :projected="projected"
        :color="color"
        :area="true"
        :end-dot="false"
        :height="28"
      />
    </div>
    <div v-else class="muted spark-empty">collecting 24h trend…</div>
    <div class="foot muted">
      <span>
        {{ limit.provenance }}
        <span v-if="isStale" class="stale" :title="`snapshot age`">
          · {{ formatDuration(limit.age_seconds) }} old
        </span>
      </span>
      <span :title="resetTitle">{{ resetText }}</span>
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
  gap: 0.5rem;
}
.stale {
  color: var(--status-warning);
}
.spark-empty {
  font-size: 0.78rem;
  height: 34px;
  display: flex;
  align-items: center;
}
.trend {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.trend-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
</style>
