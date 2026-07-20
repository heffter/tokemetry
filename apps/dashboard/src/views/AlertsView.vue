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
import {
  ALERT_KINDS,
  buildAlertConfig,
  FILTER_DIMENSIONS,
  type FilterDimension,
} from '@/lib/alerts';
import type {
  AlertEvent,
  AlertRule,
  AlertRuleInput,
  Channel,
} from '@/api/client';
import type { Limit, MachineSummary, SummaryNow } from '@/api/types';

const { loading, error, run, retry } = useAsync();
const rules = ref<AlertRule[]>([]);
const events = ref<AlertEvent[]>([]);
const summary = ref<SummaryNow | null>(null);
const machines = ref<MachineSummary[]>([]);
const formHistory = ref<number[]>([]);
const expanded = ref<Set<number>>(new Set());
const evalStatus = ref('');
const createError = ref('');

// Event severity -> filled badge class (info covered, not just warn/critical).
const SEV_BADGE: Record<string, string> = {
  info: 'badge-good',
  warning: 'badge-warning',
  critical: 'badge-critical',
  serious: 'badge-critical',
};

// Worst collector staleness across machines, for the collector_stale live value.
const worstStaleMinutes = computed(() => {
  const now = Date.now();
  let worst = 0;
  for (const machine of machines.value) {
    if (!machine.last_seen) continue;
    worst = Math.max(
      worst,
      Math.round((now - new Date(machine.last_seen).getTime()) / 60000)
    );
  }
  return worst;
});

// Kind catalog lives in lib/alerts (unit-tested, server-synced).
const KINDS = ALERT_KINDS;
const WINDOWS = [
  'five_hour',
  'seven_day',
  'seven_day_opus',
  'seven_day_sonnet',
];
const CHANNELS = ['ntfy', 'telegram', 'smtp'];

// Per-dimension filter text inputs (comma-separated values, empty = unscoped).
const filterDraft = ref<Record<FilterDimension, string>>({
  provider: '',
  model: '',
  source: '',
  project: '',
  environment: '',
});

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

function thresholds(rule: AlertRule): { warn: string; crit: string } {
  return {
    warn: rule.warn_threshold ?? rule.threshold ?? '?',
    crit: rule.crit_threshold ?? '?',
  };
}

function liveValue(rule: AlertRule): string {
  const t = thresholds(rule);
  if (rule.kind === 'limit_pct') {
    const l = currentLimit(rule.window_kind);
    if (!l) return '—';
    return `${formatPct(l.utilization_pct)} · warn ${t.warn}% / crit ${t.crit}%`;
  }
  if (rule.kind === 'burn_rate' && summary.value) {
    const rate = formatTokens(
      Math.round(summary.value.token_burn_rate_per_min)
    );
    return `${rate}/min · warn ${t.warn} / crit ${t.crit}`;
  }
  if (rule.kind === 'collector_stale') {
    return `${worstStaleMinutes.value}m idle · warn ${t.warn}m / crit ${t.crit}m`;
  }
  // Non-threshold kinds: surface a migrated legacy threshold if one exists.
  return rule.threshold ? `legacy threshold ${rule.threshold}` : '';
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

const channels = ref<Channel[]>([]);
const channelDrafts = ref<Record<string, Record<string, string>>>({});
const channelStatus = ref<Record<string, string>>({});

function fieldLabel(name: string): string {
  return name.replace(/^(ntfy|telegram|smtp)_/, '').replace(/_/g, ' ');
}

async function loadChannels(): Promise<void> {
  const report = await useClient().getChannels();
  channels.value = report.channels;
  const drafts: Record<string, Record<string, string>> = {};
  for (const ch of report.channels) {
    drafts[ch.name] = {};
    for (const f of ch.fields) {
      // Secrets start blank (their placeholder shows the masked saved value);
      // non-secrets are pre-filled and editable.
      drafts[ch.name][f.name] = f.is_secret ? '' : f.value;
    }
  }
  channelDrafts.value = drafts;
}

async function saveChannel(name: string): Promise<void> {
  channelStatus.value = { ...channelStatus.value, [name]: 'saving…' };
  const ch = channels.value.find((c) => c.name === name);
  const draft = channelDrafts.value[name] ?? {};
  const payload: Record<string, string> = {};
  for (const f of ch?.fields ?? []) {
    const value = draft[f.name] ?? '';
    // A blank secret means "leave unchanged" (omit); everything else is sent,
    // including an empty non-secret which clears back to the env default.
    if (f.is_secret && value === '') continue;
    payload[f.name] = value;
  }
  try {
    await useClient().putChannel(name, payload);
    await loadChannels();
    channelStatus.value = { ...channelStatus.value, [name]: 'saved' };
  } catch (e) {
    channelStatus.value = { ...channelStatus.value, [name]: String(e) };
  }
}

async function loadRules(): Promise<void> {
  const client = useClient();
  rules.value = await client.alertRules();
  events.value = await client.alertEvents(50);
}

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    summary.value = await client.summaryNow();
    machines.value = await client.machines();
    await loadRules();
    await loadChannels();
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
  createError.value = '';
  const name = draft.value.name.trim();
  const meta = draftMeta.value;
  const channels = draft.value.channels
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean);

  if (!name) {
    createError.value = 'A rule name is required.';
    return;
  }
  if (channels.length === 0) {
    createError.value =
      'At least one channel is required, or the rule never notifies.';
    return;
  }
  if (meta.threshold) {
    const warn = Number(draft.value.warn);
    const crit = Number(draft.value.crit);
    if (Number.isNaN(warn) || Number.isNaN(crit)) {
      createError.value = 'Warn and crit thresholds must be numbers.';
      return;
    }
    if (warn > crit) {
      createError.value =
        'The warn threshold must not exceed the crit threshold.';
      return;
    }
  }

  await useClient().createAlertRule({
    name,
    kind: draft.value.kind,
    warn_threshold: meta.threshold ? draft.value.warn : null,
    crit_threshold: meta.threshold ? draft.value.crit : null,
    window_kind: meta.window ? draft.value.window_kind : null,
    channels,
    cooldown_seconds: 3600,
    enabled: true,
    config: buildAlertConfig(filterDraft.value),
  });
  draft.value.name = '';
  for (const dim of FILTER_DIMENSIONS) filterDraft.value[dim] = '';
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
      <div class="filters">
        <span class="muted small">filters:</span>
        <input
          v-for="dim in FILTER_DIMENSIONS"
          :key="dim"
          v-model="filterDraft[dim]"
          :placeholder="dim"
          class="filter-input"
        />
      </div>
      <p v-if="createError" class="create-error">{{ createError }}</p>

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
        <Sparkline :values="formHistory" :max="100" color="var(--series-1)" />
      </div>

      <ul class="rules">
        <li v-for="rule in rules" :key="rule.id">
          <span class="mono">{{ KINDS[rule.kind]?.label ?? rule.kind }}</span>
          <span>{{ rule.name }}</span>
          <span
            class="badge"
            :class="rule.state === 'firing' ? 'badge-critical' : 'badge-good'"
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
      <h3>Notification channels</h3>
      <div v-for="ch in channels" :key="ch.name" class="channel">
        <div class="channel-head">
          <strong>{{ ch.name }}</strong>
          <span
            class="badge"
            :class="ch.configured ? 'badge-good' : 'badge-muted'"
          >
            {{ ch.configured ? 'configured' : 'not set' }}
          </span>
        </div>
        <div class="channel-fields">
          <label v-for="f in ch.fields" :key="f.name" class="field">
            <span class="muted small">{{ fieldLabel(f.name) }}</span>
            <input
              v-model="channelDrafts[ch.name][f.name]"
              :type="f.is_secret ? 'password' : 'text'"
              :placeholder="
                f.is_secret ? (f.is_set ? `saved (${f.value})` : 'not set') : ''
              "
            />
          </label>
        </div>
        <div class="channel-actions">
          <button @click="saveChannel(ch.name)">Save</button>
          <button class="ghost" @click="testChannel(ch.name)">Test</button>
          <span v-if="channelStatus[ch.name]" class="muted small">{{
            channelStatus[ch.name]
          }}</span>
        </div>
      </div>
    </section>

    <section class="card">
      <h3>Recent alerts</h3>
      <ul class="events">
        <li v-for="event in events" :key="event.id">
          <div class="event-head" @click="toggleExpand(event.id)">
            <span
              class="badge"
              :class="SEV_BADGE[event.severity] ?? 'badge-muted'"
              >{{ event.severity }}</span
            >
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
.filters {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}
.filter-input {
  width: 120px;
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
.create-error {
  color: var(--status-critical);
  font-size: 0.85rem;
  margin: 0.25rem 0 0.5rem;
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
.channel {
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--border);
}
.channel:last-child {
  border-bottom: none;
}
.channel-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  text-transform: capitalize;
}
.channel-fields {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.field input {
  width: 180px;
}
.channel-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
</style>
