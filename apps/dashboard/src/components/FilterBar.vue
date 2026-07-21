<script setup lang="ts">
// A shared filter row: date-range presets (plus custom from/to) and optional
// provider, model, machine, and project selectors. Emits the resolved
// UsageFilter whenever any control changes, so parent views can pass it
// straight to the usage API. Provider and model are the cross-view global
// filter (FR-UI-002): their state lives in useGlobalFilters, so a selection
// made here carries to every other view.
import { computed, ref, watch } from 'vue';
import { PRESETS, presetRange } from '@/lib/filters';
import type { RangePreset, UsageFilter } from '@/lib/filters';
import { useGlobalFilters } from '@/composables/useGlobalFilters';

/** A registry-labeled dropdown option (value is the id, label the display name). */
export interface SelectOption {
  value: string;
  label: string;
}

withDefaults(
  defineProps<{
    machines?: string[];
    projects?: string[];
    providers?: SelectOption[];
    models?: SelectOption[];
  }>(),
  {
    machines: () => [],
    projects: () => [],
    providers: () => [],
    models: () => [],
  }
);

const emit = defineEmits<{ change: [filter: UsageFilter] }>();

const { provider, model, setProvider, setModel } = useGlobalFilters();
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
    provider: provider.value || undefined,
    model: model.value || undefined,
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

    <select
      v-if="providers.length"
      :value="provider"
      aria-label="provider"
      @change="setProvider(($event.target as HTMLSelectElement).value)"
    >
      <option value="">all providers</option>
      <option v-for="p in providers" :key="p.value" :value="p.value">
        {{ p.label }}
      </option>
    </select>
    <select
      v-if="models.length"
      :value="model"
      aria-label="model"
      @change="setModel(($event.target as HTMLSelectElement).value)"
    >
      <option value="">all models</option>
      <option v-for="m in models" :key="m.value" :value="m.value">
        {{ m.label }}
      </option>
    </select>

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
  padding: 0.7rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-elevated) 86%, transparent);
  box-shadow: var(--shadow-sm);
}
.presets,
.custom {
  display: flex;
  gap: 0.25rem;
  align-items: center;
  flex-wrap: wrap;
}
button,
select,
input {
  font: inherit;
  min-height: 36px;
  padding: 0.35rem 0.7rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text-secondary);
  cursor: pointer;
}
button.active {
  border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
  background: var(--nav-active);
  color: var(--text-primary);
}
select {
  color: var(--text-primary);
  min-width: 148px;
}
</style>
