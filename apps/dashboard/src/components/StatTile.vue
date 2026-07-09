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
  gap: 0.25rem;
}
.label {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.value {
  font-size: 1.9rem;
  font-weight: 600;
  line-height: 1.1;
}
.sub {
  font-size: 0.85rem;
}
</style>
