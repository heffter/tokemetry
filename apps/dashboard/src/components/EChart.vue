<script setup lang="ts">
// Thin ECharts wrapper: init on mount, update on option change, resize with
// the container, dispose on unmount. Keeps chart lifecycle out of views.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';

const props = defineProps<{
  option: echarts.EChartsCoreOption;
  height?: string;
}>();

// Surfaces a legend toggle so the parent view can persist the selection; the
// option is rebuilt with notMerge on every update, which would otherwise wipe
// the user's choice, so selection state must live in the view.
const emit = defineEmits<{
  'legend-select': [selected: Record<string, boolean>];
}>();

const container = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

function render(): void {
  if (chart && props.option) {
    chart.setOption(props.option, true);
  }
}

function resize(): void {
  chart?.resize();
}

onMounted(() => {
  if (container.value) {
    chart = echarts.init(container.value);
    chart.on('legendselectchanged', (params: unknown) => {
      const selected = (params as { selected?: Record<string, boolean> })
        .selected;
      if (selected) emit('legend-select', { ...selected });
    });
    render();
    window.addEventListener('resize', resize);
  }
});

watch(
  () => props.option,
  () => render(),
  { deep: true }
);

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize);
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div
    ref="container"
    :style="{ height: height ?? '320px', width: '100%' }"
  ></div>
</template>
