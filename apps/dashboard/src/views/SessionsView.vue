<script setup lang="ts">
// Recent sessions table.
import { onMounted, ref } from 'vue';
import { useClient } from '@/composables/useApi';
import { formatCost, formatTokens } from '@/lib/format';
import type { SessionSummary } from '@/api/types';

const sessions = ref<SessionSummary[]>([]);
const error = ref('');

async function load(): Promise<void> {
  try {
    sessions.value = await useClient().sessions(200);
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <section v-else class="card">
    <h3>Recent sessions</h3>
    <table>
      <thead>
        <tr>
          <th>Session</th>
          <th>Project</th>
          <th>Machine</th>
          <th class="num">Messages</th>
          <th class="num">Tokens</th>
          <th class="num">Cost</th>
          <th>Last active</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in sessions" :key="s.session_id">
          <td class="mono">{{ s.session_id.slice(0, 8) }}</td>
          <td>{{ s.project ?? '—' }}</td>
          <td>{{ s.machine ?? '—' }}</td>
          <td class="num tabular">{{ s.message_count }}</td>
          <td class="num tabular">{{ formatTokens(s.total_tokens) }}</td>
          <td class="num tabular">{{ formatCost(s.cost_usd) }}</td>
          <td>{{ new Date(s.last_at).toLocaleString() }}</td>
        </tr>
        <tr v-if="sessions.length === 0">
          <td colspan="7" class="muted">No sessions yet.</td>
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
  font-size: 0.9rem;
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
.mono {
  font-family: ui-monospace, monospace;
}
</style>
