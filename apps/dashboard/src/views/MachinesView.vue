<script setup lang="ts">
// Fleet health: registered machines, last-seen staleness, and totals.
import { computed, onMounted, ref } from 'vue';
import { useClient } from '@/composables/useApi';
import { formatTokens } from '@/lib/format';
import type { MachineSummary } from '@/api/types';

const machines = ref<MachineSummary[]>([]);
const error = ref('');
const now = computed(() => Date.now());

function staleness(lastSeen: string | null): string {
  if (lastSeen === null) return 'never';
  const minutes = Math.round(
    (now.value - new Date(lastSeen).getTime()) / 60000
  );
  if (minutes < 2) return 'online';
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function stale(lastSeen: string | null): boolean {
  if (lastSeen === null) return true;
  return now.value - new Date(lastSeen).getTime() > 30 * 60000;
}

async function load(): Promise<void> {
  try {
    machines.value = await useClient().machines();
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <section v-else class="card">
    <h3>Machines</h3>
    <table>
      <thead>
        <tr>
          <th>Machine</th>
          <th>Platform</th>
          <th>Collector</th>
          <th class="num">Events</th>
          <th class="num">Tokens</th>
          <th>Last seen</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="m in machines" :key="m.id">
          <td>{{ m.id }}</td>
          <td>{{ m.platform ?? '—' }}</td>
          <td>{{ m.collector_version ?? '—' }}</td>
          <td class="num tabular">{{ m.event_count }}</td>
          <td class="num tabular">{{ formatTokens(m.total_tokens) }}</td>
          <td :class="{ stale: stale(m.last_seen) }">
            {{ staleness(m.last_seen) }}
          </td>
        </tr>
        <tr v-if="machines.length === 0">
          <td colspan="6" class="muted">No machines enrolled yet.</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
h3 {
  margin: 0 0 1rem;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}
.num {
  text-align: right;
}
.stale {
  color: var(--status-critical);
  font-weight: 600;
}
</style>
