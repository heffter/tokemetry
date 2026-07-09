<script setup lang="ts">
// Alert rules and recent alert history. Create common rules, toggle-delete,
// and trigger an on-demand evaluation.
import { onMounted, ref } from 'vue';
import { useClient } from '@/composables/useApi';
import type { AlertEvent, AlertRule } from '@/api/client';

const rules = ref<AlertRule[]>([]);
const events = ref<AlertEvent[]>([]);
const error = ref('');

const draft = ref({
  name: '',
  kind: 'limit_pct',
  threshold: '80',
  window_kind: 'five_hour',
  channels: 'ntfy',
});
const kinds = [
  'limit_pct',
  'predicted_exhaustion',
  'burn_rate',
  'collector_stale',
  'unknown_model',
];

async function load(): Promise<void> {
  try {
    const client = useClient();
    rules.value = await client.alertRules();
    events.value = await client.alertEvents(50);
  } catch (e) {
    error.value = String(e);
  }
}

async function create(): Promise<void> {
  if (!draft.value.name.trim()) return;
  await useClient().createAlertRule({
    name: draft.value.name.trim(),
    kind: draft.value.kind,
    threshold: draft.value.threshold || null,
    window_kind: draft.value.window_kind || null,
    channels: draft.value.channels
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean),
    cooldown_seconds: 3600,
    enabled: true,
  });
  draft.value.name = '';
  await load();
}

async function remove(id: number): Promise<void> {
  await useClient().deleteAlertRule(id);
  await load();
}

async function evaluate(): Promise<void> {
  await useClient().evaluateAlerts();
  await load();
}

onMounted(load);
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <template v-else>
    <section class="card">
      <div class="head">
        <h3>Alert rules</h3>
        <button @click="evaluate">Evaluate now</button>
      </div>
      <div class="form">
        <input v-model="draft.name" placeholder="rule name" />
        <select v-model="draft.kind">
          <option v-for="k in kinds" :key="k" :value="k">{{ k }}</option>
        </select>
        <input
          v-model="draft.threshold"
          placeholder="threshold"
          class="narrow"
        />
        <input v-model="draft.channels" placeholder="channels" class="narrow" />
        <button @click="create">Add</button>
      </div>
      <ul class="rules">
        <li v-for="rule in rules" :key="rule.id">
          <span class="mono">{{ rule.kind }}</span>
          <span>{{ rule.name }}</span>
          <span class="muted">{{
            rule.channels.join(', ') || 'no channel'
          }}</span>
          <button class="link" @click="remove(rule.id)">delete</button>
        </li>
        <li v-if="rules.length === 0" class="muted">No rules configured.</li>
      </ul>
    </section>

    <section class="card">
      <h3>Recent alerts</h3>
      <ul class="events">
        <li v-for="event in events" :key="event.id">
          <span class="sev" :class="event.severity">{{ event.severity }}</span>
          <span>{{ event.title }}</span>
          <span class="muted">{{ event.delivered ? 'sent' : 'not sent' }}</span>
        </li>
        <li v-if="events.length === 0" class="muted">No alerts yet.</li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
h3 {
  margin: 0 0 1rem;
}
.form {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
input,
select,
button {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
.narrow {
  width: 110px;
}
button {
  cursor: pointer;
}
.rules,
.events {
  list-style: none;
  padding: 0;
  margin: 0;
}
.rules li,
.events li {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.45rem 0;
  border-bottom: 1px solid var(--border);
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.link {
  border: none;
  background: none;
  color: var(--status-critical);
  cursor: pointer;
  margin-left: auto;
}
.sev {
  text-transform: uppercase;
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
}
.sev.warning {
  color: var(--status-warning);
}
.sev.critical {
  color: var(--status-critical);
}
.sev.serious {
  color: var(--status-critical);
}
</style>
