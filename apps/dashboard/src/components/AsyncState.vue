<script setup lang="ts">
// Wraps a section's content with consistent loading, empty, and error states.
// Error shows a human message and a Retry button; empty only shows after a
// successful load, so a fetch-in-progress never looks like "no data".
withDefaults(
  defineProps<{
    loading: boolean;
    error: string | null;
    empty?: boolean;
    emptyText?: string;
  }>(),
  { empty: false, emptyText: 'No data yet.' }
);
defineEmits<{ retry: [] }>();
</script>

<template>
  <div v-if="error" class="card state">
    <p class="msg">{{ error }}</p>
    <button class="retry" @click="$emit('retry')">Retry</button>
  </div>
  <div v-else-if="loading" class="card state">
    <div v-for="n in 3" :key="n" class="skeleton"></div>
  </div>
  <div v-else-if="empty" class="card state muted">{{ emptyText }}</div>
  <slot v-else />
</template>

<style scoped>
.state {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.msg {
  margin: 0;
  color: var(--status-critical);
}
.retry {
  align-self: flex-start;
  font: inherit;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  cursor: pointer;
}
.skeleton {
  height: 1.2rem;
  border-radius: 6px;
  background: linear-gradient(
    90deg,
    var(--gridline) 25%,
    var(--surface) 50%,
    var(--gridline) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}
.skeleton:first-child {
  height: 2rem;
  width: 40%;
}
@keyframes shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}
</style>
