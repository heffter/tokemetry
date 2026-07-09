<script setup lang="ts">
// A shared filter row: date-range presets (plus custom from/to) and optional
// machine and project selectors. Emits the resolved UsageFilter whenever any
// control changes, so parent views can pass it straight to the usage API.
import { computed, ref, watch } from 'vue';
import { PRESETS, presetRange } from '@/lib/filters';
import type { RangePreset, UsageFilter } from '@/lib/filters';

withDefaults(
  defineProps<{
    machines?: string[];
    projects?: string[];
  }>(),
  { machines: () => [], projects: () => [] }
);

const emit = defineEmits<{ change: [filter: UsageFilter] }>();

const preset = ref<RangePreset>('30d');
const customFrom = ref('');
const customTo = ref('');
const machine = ref('');
const project = ref('');

const filter = computed<UsageFilter>(() => {
  const range =
    preset.value === 'custom'
      ? { from: customFrom.value || undefined, to: customTo.value || undefined }
      : presetRange(preset.value);
  return {
    from: range.from,
    to: range.to,
    machine: machine.value || undefined,
    project: project.value || undefined,
  };
});

function choose(next: RangePreset): void {
  preset.value = next;
}

watch(filter, (value) => emit('change', value), { immediate: true });
</script>

<template>
  <div class="filterbar">
    <div class="presets">
      <button
        v-for="p in PRESETS"
        :key="p.key"
        :class="{ active: preset === p.key }"
        @click="choose(p.key)"
      >
        {{ p.label }}
      </button>
      <button
        :class="{ active: preset === 'custom' }"
        @click="choose('custom')"
      >
        Custom
      </button>
    </div>

    <div v-if="preset === 'custom'" class="custom">
      <input v-model="customFrom" type="date" aria-label="from date" />
      <span class="muted">to</span>
      <input v-model="customTo" type="date" aria-label="to date" />
    </div>

    <select v-if="machines.length" v-model="machine" aria-label="machine">
      <option value="">all machines</option>
      <option v-for="m in machines" :key="m" :value="m">{{ m }}</option>
    </select>
    <select v-if="projects.length" v-model="project" aria-label="project">
      <option value="">all projects</option>
      <option v-for="p in projects" :key="p" :value="p">{{ p }}</option>
    </select>
  </div>
</template>

<style scoped>
.filterbar {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.presets,
.custom {
  display: flex;
  gap: 0.25rem;
  align-items: center;
}
button,
select,
input {
  font: inherit;
  padding: 0.3rem 0.7rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-secondary);
  cursor: pointer;
}
button.active {
  background: var(--gridline);
  color: var(--text-primary);
}
select {
  color: var(--text-primary);
}
</style>
