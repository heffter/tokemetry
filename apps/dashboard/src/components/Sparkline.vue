<script setup lang="ts">
// A tiny inline-SVG sparkline of utilization over time, with an optional
// dashed projection segment and an end marker. No chart library needed.
import { computed } from 'vue';
import { sparkGeometry } from '@/lib/sparkline';

const props = withDefaults(
  defineProps<{
    values: number[];
    max?: number;
    projected?: number | null;
    color?: string;
    width?: number;
    height?: number;
    markerIndex?: number | null;
  }>(),
  {
    max: 100,
    projected: null,
    color: 'var(--text-muted)',
    width: 220,
    height: 34,
    markerIndex: null,
  }
);

const geo = computed(() =>
  sparkGeometry(
    props.values,
    props.width,
    props.height,
    props.max,
    props.projected
  )
);

// A vertical guide at a notable index (e.g. a session's inflection turn).
const markerX = computed(() => {
  const index = props.markerIndex;
  if (index === null || index === undefined || props.values.length < 2) {
    return null;
  }
  return (index / (props.values.length - 1)) * props.width;
});
</script>

<template>
  <svg
    v-if="geo.last"
    :viewBox="`0 0 ${width} ${height}`"
    :width="width"
    :height="height"
    class="spark"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <polyline
      :points="geo.points"
      fill="none"
      :stroke="color"
      stroke-width="2"
      stroke-linejoin="round"
    />
    <polyline
      v-if="geo.projection"
      :points="geo.projection"
      fill="none"
      :stroke="color"
      stroke-width="2"
      stroke-dasharray="3 3"
      opacity="0.7"
    />
    <line
      v-if="markerX !== null"
      :x1="markerX"
      :y1="0"
      :x2="markerX"
      :y2="height"
      stroke="var(--status-warning)"
      stroke-width="1.5"
      stroke-dasharray="2 2"
    />
    <circle :cx="geo.last[0]" :cy="geo.last[1]" r="2.5" :fill="color" />
  </svg>
</template>

<style scoped>
.spark {
  display: block;
  width: 100%;
  height: auto;
}
</style>
