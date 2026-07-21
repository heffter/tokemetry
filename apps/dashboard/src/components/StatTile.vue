<script setup lang="ts">
// A labelled headline figure with an optional sublabel.
defineProps<{
  label: string;
  value: string;
  sub?: string;
}>();
</script>

<template>
  <div class="card tile">
    <div class="muted label">{{ label }}</div>
    <div class="value tabular">{{ value }}</div>
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
}
.value {
  font-size: clamp(1.45rem, 2.4vw, 2rem);
  font-weight: 760;
  line-height: 1.1;
  overflow-wrap: anywhere;
}
.sub {
  font-size: 0.85rem;
  line-height: 1.35;
}
</style>
