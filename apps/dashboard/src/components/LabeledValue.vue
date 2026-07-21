<script setup lang="ts">
// A labelled headline figure that renders a resolved ValueState, so zero,
// unavailable, and unsupported read distinctly (FR-DIM-010) rather than all
// collapsing to a bare number or dash. An optional provenance badge marks a
// value as an official reading or an estimate (FR-UI-012). This is the
// provider-neutral counterpart to StatTile (which takes a pre-formatted
// string); views pass a ValueState resolved via lib/valueState.
import { computed } from 'vue';
import type { Provenance, ValueState } from '@/lib/valueState';
import { provenanceLabel, valueDisplay } from '@/lib/valueState';

const props = withDefaults(
  defineProps<{
    label: string;
    state: ValueState;
    sub?: string;
    provenance?: Provenance;
  }>(),
  { sub: '', provenance: null }
);

const display = computed(() => valueDisplay(props.state));
const badge = computed(() => provenanceLabel(props.provenance));
</script>

<template>
  <div class="card tile">
    <div class="muted label">
      {{ label }}
      <span v-if="badge" class="badge" :class="`prov-${provenance}`">{{
        badge
      }}</span>
    </div>
    <div
      class="value tabular"
      :class="{ muted: display.muted }"
      :title="display.title"
    >
      {{ display.text }}
    </div>
    <div v-if="sub" class="muted sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.tile {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-height: 116px;
  justify-content: space-between;
}
.label {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 800;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.value {
  font-size: clamp(1.45rem, 2.4vw, 2rem);
  font-weight: 760;
  line-height: 1.1;
  overflow-wrap: anywhere;
}
.value.muted {
  font-weight: 500;
}
.sub {
  font-size: 0.85rem;
  line-height: 1.35;
}
.badge {
  font-size: 0.6rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.05rem 0.35rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.prov-estimated {
  color: var(--status-warning);
}
</style>
