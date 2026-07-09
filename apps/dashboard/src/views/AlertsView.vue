<script setup lang="ts">
// Alerts: kind-aware thresholds set against the live values they watch, a
// status board of rules with current-vs-threshold, enable/disable, and an
// alert history with timestamps and expandable context.
import { computed, onMounted, ref, watch } from 'vue';
import AsyncState from '@/components/AsyncState.vue';
import Sparkline from '@/components/Sparkline.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  formatDateTime,
  formatPct,
  formatTokens,
  timeAgo,
  windowLabel,
} from '@/lib/format';
import type { AlertEvent, AlertRule, AlertRuleInput } from '@/api/client';
import type { Limit, SummaryNow } from '@/api/types';

const { loading, error, run, retry } = useAsync();
const rules = ref<AlertRule[]>([]);
const events = ref<AlertEvent[]>([]);
const summary = ref<SummaryNow | null>(null);
const formHistory = ref<number[]>([]);
const expanded = ref<Set<number>>(new Set());
const evalStatus = ref('');

interface KindMeta {
  label: string;
  window: boolean;
  // Dual warn/critical thresholds; null for kinds that fire on a boolean state.
  threshold: {
    min?: number;
    max?: number;
    suffix: string;
    warnDef: string;
    critDef: string;
  } | null;
}

const KINDS: Record<string, KindMeta> = {
  limit_pct: {
    label: 'Limit %',
    window: true,
    threshold: { min: 0, max: 100, suffix: '%', warnDef: '80', critDef: '95' },
  },
  burn_rate: {
    label: 'Burn rate',
    window: false,
    threshold: { suffix: 'tok/min', warnDef: '5000', critDef: '10000' },
  },
  predicted_exhaustion: {
    label: 'Predicted exhaustion',
    window: false,
    threshold: null,
  },
  collector_stale: {
    label: 'Collector stale',
    window: false,
    threshold: { suffix: 'min', warnDef: '30', critDef: '120' },
  },
  unknown_model: { label: 'Unpriced usage', window: false, threshold: null },
};
const WINDOWS = [
  'five_hour',
  'seven_day',
  'seven_day_opus',
  'seven_day_sonnet',
];
const CHANNELS = ['ntfy', 'telegram', 'smtp'];

const draft = ref({
  name: '',
  kind: 'limit_pct',
  window_kind: 'five_hour',
  warn: '80',
  crit: '95',
  channels: 'ntfy',
});

const draftMeta = computed(() => KINDS[draft.value.kind]);
const testStatus = ref('');

watch(
  () => draft.value.kind,
  (kind) => {
    draft.value.warn = KINDS[kind].threshold?.warnDef ?? '';
    draft.value.crit = KINDS[kind].threshold?.critDef ?? '';
  }
);

function currentLimit(windowKind: string | null): Limit | undefined {
  return summary.value?.limits.find((l) => l.window_kind === windowKind);
}

function ruleThreshold(rule: AlertRule): string {
  return rule.warn_threshold ?? rule.threshold ?? '?';
}

function liveValue(rule: AlertRule): string {
  if (rule.kind === 'limit_pct') {
    const l = currentLimit(rule.window_kind);
    if (!l) return '—';
    return `${formatPct(l.utilization_pct)} / ${ruleThreshold(rule)}%`;
  }
  if (rule.kind === 'burn_rate' && summary.value) {
    return `${formatTokens(Math.round(summary.value.token_burn_rate_per_min))}/min / ${ruleThreshold(rule)}`;
  }
  return '';
}

function ruleInput(rule: AlertRule, enabled: boolean): AlertRuleInput {
  return {
    name: rule.name,
    kind: rule.kind,
    threshold: rule.threshold,
    warn_threshold: rule.warn_threshold,
    crit_threshold: rule.crit_threshold,
    window_kind: rule.window_kind,
    channels: rule.channels,
    cooldown_seconds: rule.cooldown_seconds,
    enabled,
  };
}

async function testChannel(channel: string): Promise<void> {
  testStatus.value = `testing ${channel}…`;
  const result = await useClient().testChannel(channel);
  testStatus.value = result.delivered
    ? `${channel}: test sent`
    : `${channel}: not configured or failed`;
}

async function loadRules(): Promise<void> {
  const client = useClient();
  rules.value = await client.alertRules();
  events.value = await client.alertEvents(50);
}

async function load(): Promise<void> {
  await run(async () => {
    summary.value = await useClient().summaryNow();
    await loadRules();
  });
}

async function loadFormHistory(): Promise<void> {
  if (draft.value.kind !== 'limit_pct') {
    formHistory.value = [];
    return;
  }
  try {
    const rows = await useClient().limitsHistory(draft.value.window_kind, 24);
    formHistory.value = rows.map((r) => r.utilization_pct);
  } catch {
    formHistory.value = [];
  }
}

watch(() => [draft.value.kind, draft.value.window_kind], loadFormHistory);

async function create(): Promise<void> {
  if (!draft.value.name.trim()) return;
  const meta = draftMeta.value;
  await useClient().createAlertRule({
    name: draft.value.name.trim(),
    kind: draft.value.kind,
    warn_threshold: meta.threshold ? draft.value.warn : null,
    crit_threshold: meta.threshold ? draft.value.crit : null,
    window_kind: meta.window ? draft.value.window_kind : null,
    channels: draft.value.channels
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean),
    cooldown_seconds: 3600,
    enabled: true,
  });
  draft.value.name = '';
  await loadRules();
}

async function toggle(rule: AlertRule): Promise<void> {
  await useClient().updateAlertRule(rule.id, ruleInput(rule, !rule.enabled));
  await loadRules();
}

async function remove(rule: AlertRule): Promise<void> {
  if (!window.confirm(`Delete alert rule "${rule.name}"?`)) return;
  await useClient().deleteAlertRule(rule.id);
  await loadRules();
}

async function evaluate(): Promise<void> {
  evalStatus.value = 'evaluating…';
  const result = await useClient().evaluateAlerts();
  evalStatus.value =
    result.fired.length > 0
      ? `${result.fired.length} rule(s) fired`
      : 'nothing fired';
  await loadRules();
}

function toggleExpand(id: number): void {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

onMounted(() => {
  void load();
  void loadFormHistory();
});
</script>

<template>
  <AsyncState
    :loading="loading && rules.length === 0"
    :error="error"
    @retry="retry"
  >
    <section class="card">
      <div class="head">
        <h3>Alert rules</h3>
        <div class="row">
          <span v-if="evalStatus" class="muted">{{ evalStatus }}</span>
          <button @click="evaluate">Evaluate now</button>
        </div>
      </div>

      <div class="form">
        <input v-model="draft.name" placeholder="rule name" class="wide" />
        <select v-model="draft.kind">
          <option v-for="(m, k) in KINDS" :key="k" :value="k">
            {{ m.label }}
          </option>
        </select>
        <select v-if="draftMeta.window" v-model="draft.window_kind">
          <option v-for="w in WINDOWS" :key="w" :value="w">
            {{ windowLabel(w) }}
          </option>
        </select>
        <label v-if="draftMeta.threshold" class="threshold">
          <span class="muted small">warn</span>
          <input
            v-model="draft.warn"
            type="number"
            :min="draftMeta.threshold.min"
            :max="draftMeta.threshold.max"
            class="thin"
          />
          <span class="muted small">crit</span>
          <input
            v-model="draft.crit"
            type="number"
            :min="draftMeta.threshold.min"
            :max="draftMeta.threshold.max"
            class="thin"
          />
          <span class="muted small">{{ draftMeta.threshold.suffix }}</span>
        </label>
        <input v-model="draft.channels" placeholder="channels" class="narrow" />
        <button @click="create">Add</button>
      </div>

      <div class="channels-test">
        <span class="muted small">test channel:</span>
        <button
          v-for="c in CHANNELS"
          :key="c"
          class="ghost"
          @click="testChannel(c)"
        >
          {{ c }}
        </button>
        <span v-if="testStatus" class="muted small">{{ testStatus }}</span>
      </div>
      <div
        v-if="draft.kind === 'limit_pct' && formHistory.length > 1"
        class="spark"
      >
        <span class="muted"
          >{{ windowLabel(draft.window_kind) }} (last 24h):</span
        >
        <Sparkline
          :values="formHistory"
          :max="100"
          color="var(--series-1, #2a78d6)"
        />
      </div>

      <ul class="rules">
        <li v-for="rule in rules" :key="rule.id">
          <span class="mono">{{ KINDS[rule.kind]?.label ?? rule.kind }}</span>
          <span>{{ rule.name }}</span>
          <span
            class="state"
            :class="rule.state === 'firing' ? 'firing' : 'ok'"
            :title="
              rule.last_fired_at ? formatDateTime(rule.last_fired_at) : ''
            "
          >
            {{ rule.state === 'firing' ? 'firing' : 'ok' }}
          </span>
          <span class="muted live">{{ liveValue(rule) }}</span>
          <span class="muted">{{
            rule.channels.join(', ') || 'no channel'
          }}</span>
          <label class="switch">
            <input
              type="checkbox"
              :checked="rule.enabled"
              @change="toggle(rule)"
            />
            {{ rule.enabled ? 'on' : 'off' }}
          </label>
          <button class="link" @click="remove(rule)">delete</button>
        </li>
        <li v-if="rules.length === 0" class="muted">No rules configured.</li>
      </ul>
    </section>

    <section class="card">
      <h3>Recent alerts</h3>
      <ul class="events">
        <li v-for="event in events" :key="event.id">
          <div class="event-head" @click="toggleExpand(event.id)">
            <span class="sev" :class="event.severity">{{
              event.severity
            }}</span>
            <span class="title">{{ event.title }}</span>
            <span class="muted" :title="formatDateTime(event.ts)">
              {{ timeAgo(event.ts) }}
            </span>
            <span class="muted">{{
              event.delivered ? 'sent' : 'not sent'
            }}</span>
          </div>
          <div v-if="expanded.has(event.id)" class="event-body">
            <p>{{ event.body }}</p>
            <pre class="context">{{
              JSON.stringify(event.context, null, 2)
            }}</pre>
          </div>
        </li>
        <li v-if="events.length === 0" class="muted">No alerts yet.</li>
      </ul>
    </section>
  </AsyncState>
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
.row {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}
h3 {
  margin: 0 0 1rem;
}
.form {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 0.5rem;
}
.threshold {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.thin {
  width: 64px;
}
.small {
  font-size: 0.8rem;
}
.channels-test {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}
.ghost {
  padding: 0.25rem 0.6rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.state {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
}
.state.ok {
  color: var(--status-good);
}
.state.firing {
  color: var(--status-critical);
}
.spark {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  max-width: 320px;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
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
input[type='range'] {
  padding: 0;
}
.wide {
  flex: 1;
  min-width: 140px;
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
  margin: 1rem 0 0;
}
.rules li {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
}
.live {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.mono {
  font-size: 0.85rem;
  min-width: 130px;
}
.switch {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.link {
  border: none;
  background: none;
  color: var(--status-critical);
  cursor: pointer;
}
.events li {
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
}
.event-head {
  display: flex;
  gap: 1rem;
  align-items: center;
  cursor: pointer;
}
.title {
  flex: 1;
}
.event-body {
  margin-top: 0.5rem;
  font-size: 0.9rem;
}
.context {
  background: var(--page);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem;
  overflow-x: auto;
  font-size: 0.8rem;
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
.sev.critical,
.sev.serious {
  color: var(--status-critical);
}
</style>
