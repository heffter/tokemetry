<script setup lang="ts">
// Front page: live limit gauges, burn rate, prediction, today by model, and a
// live activity feed over the WebSocket stream.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import GaugeCard from '@/components/GaugeCard.vue';
import StatTile from '@/components/StatTile.vue';
import EChart from '@/components/EChart.vue';
import ChartTable from '@/components/ChartTable.vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient, useToken } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  stackedTokenBarOption,
  tokenTableRows,
  TOKEN_TABLE_HEADERS,
} from '@/lib/charts';
import {
  cacheReadShare,
  formatCost,
  formatDuration,
  formatPct,
  formatTokens,
  modelLabel,
  timeUntil,
  utilizationStatus,
  windowLabel,
} from '@/lib/format';

// A limit snapshot older than this is treated as stale (collector not polling).
const STALE_SECONDS = 600;
const STATUS_WORD: Record<string, string> = {
  good: 'OK',
  warning: 'Warning',
  critical: 'Critical',
};
import { costIsTrustworthy, pricedCoverage } from '@/lib/coverage';
import { loadSelection, saveSelection } from '@/composables/useSettings';
import { throttle } from '@/lib/throttle';
import type { CostResponse, StreamMessage } from '@/api/types';
import type { SummaryNow } from '@/api/types';

interface FeedRow {
  id: number;
  text: string;
}

const { loading, error, run, retry } = useAsync();
const summary = ref<SummaryNow | null>(null);
const cost = ref<CostResponse | null>(null);
const histories = ref<Record<string, number[]>>({});
const feed = ref<FeedRow[]>([]);

// The single most-at-risk window, promoted to a headline banner. Stale limit
// data is called out as stale rather than asserted as current risk.
const atRisk = computed(() => {
  const s = summary.value;
  if (!s || s.limits.length === 0) return null;
  const p = s.prediction;
  if (p && p.predicted_exhaustion_at) {
    return {
      level: 'critical',
      label: 'Critical',
      text: `You will hit your ${windowLabel(p.window_kind)} in ${timeUntil(p.predicted_exhaustion_at)} — ${p.utilization_pct.toFixed(0)}%, climbing ${p.slope_pct_per_min.toFixed(1)}%/min`,
    };
  }
  const top = [...s.limits].sort(
    (a, b) => b.utilization_pct - a.utilization_pct
  )[0];
  if (top.age_seconds >= STALE_SECONDS) {
    return {
      level: 'warning',
      label: 'Stale',
      text: `Limit data is ${formatDuration(top.age_seconds)} old — the collector last reported then, so live utilization is unknown.`,
    };
  }
  const level = utilizationStatus(top.utilization_pct);
  return {
    level,
    label: STATUS_WORD[level],
    text: `${windowLabel(top.window_kind)} at ${formatPct(top.utilization_pct)} — resets ${timeUntil(top.resets_at)}`,
  };
});

function projectedFor(windowKind: string): number | null {
  const p = summary.value?.prediction;
  return p && p.window_kind === windowKind && p.predicted_exhaustion_at
    ? 100
    : null;
}
const updatedAt = ref<number>(0);
const tick = ref<number>(Date.now());
let feedSeq = 0;
let socket: WebSocket | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;

const updatedAgo = computed(() => {
  if (updatedAt.value === 0) return '';
  const secs = Math.max(0, Math.round((tick.value - updatedAt.value) / 1000));
  return `updated ${secs}s ago`;
});

// Composition (normalized) so cache-read dominance does not crush the other
// components; the ChartTable below carries the absolute magnitudes. Cache-read
// is deselected by default (it dominates and is misleading in a composition);
// hiding it re-normalizes the rest to 100%.
const selection = ref(loadSelection('now-model', { 'cache read': false }));
function onLegend(sel: Record<string, boolean>): void {
  selection.value = sel;
  saveSelection('now-model', sel);
}
const modelChart = computed(() => {
  const models = summary.value?.today.by_model ?? [];
  return stackedTokenBarOption(
    models.map((m) => modelLabel(m.key)),
    models,
    { normalized: true, selected: selection.value }
  );
});

const cacheShare = computed(() =>
  cacheReadShare(summary.value?.today.by_model ?? [])
);

// Guard the cache tile so a no-usage day reads "no usage yet", not a fake 0.0%.
const cacheTile = computed(() => {
  const models = summary.value?.today.by_model ?? [];
  const total = models.reduce((sum, m) => sum + m.total_tokens, 0);
  if (total === 0) return { value: '—', sub: 'no usage yet today' };
  return {
    value: formatPct(cacheShare.value * 100),
    sub: "of today's tokens served from cache",
  };
});

// Coverage of today's cost: never present a bare dollar figure derived from
// a price table that does not price every model in use.
const todayCoverage = computed(() =>
  pricedCoverage(summary.value?.today.by_model ?? [])
);

const todayCostSub = computed(() => {
  const cov = todayCoverage.value;
  if (cov.totalTokens === 0) return 'no usage yet today';
  if (costIsTrustworthy(cov)) {
    return `${formatCost(summary.value?.today.cost_usd ?? null)} equivalent`;
  }
  return `cost partial — ${cov.unpricedKeys.length} model(s) unpriced`;
});

const valueTile = computed(() => {
  const c = cost.value;
  if (!c) return { value: '—', sub: '' };
  if (c.value_multiple !== null) {
    return {
      value: `${c.value_multiple.toFixed(1)}x`,
      sub: `vs $${c.subscription_monthly_usd}/mo · last 30d · priced only`,
    };
  }
  return {
    value: formatCost(c.total_cost_usd),
    sub: 'equivalent, last 30 days',
  };
});

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    summary.value = await client.summaryNow();
    cost.value = await client.cost();
    const hist: Record<string, number[]> = {};
    await Promise.all(
      summary.value.limits.map(async (l) => {
        const rows = await client.limitsHistory(l.window_kind, 24);
        hist[l.window_kind] = rows.map((r) => r.utilization_pct);
      })
    );
    histories.value = hist;
    updatedAt.value = Date.now();
  });
}

// Coalesce a burst of ingest events into at most one summary refetch per 10s,
// so the live stream never triggers a per-event refetch storm.
const throttledLoad = throttle(() => void load(), 10000);

function connectStream(): void {
  const { token } = useToken();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(
    `${proto}://${location.host}/api/v1/stream?token=${token.value}`
  );
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data) as StreamMessage;
    feed.value.unshift({
      id: feedSeq++,
      text: `${message.machine}: ${message.accepted} ${message.type}`,
    });
    feed.value = feed.value.slice(0, 12);
    throttledLoad();
  };
}

onMounted(() => {
  void load();
  connectStream();
  // Fallback poll so numbers do not silently freeze on an idle or dropped
  // socket, plus a 1s tick to keep the "updated Xs ago" indicator moving.
  pollTimer = setInterval(() => void load(), 30000);
  tickTimer = setInterval(() => (tick.value = Date.now()), 1000);
});

onBeforeUnmount(() => {
  socket?.close();
  if (pollTimer) clearInterval(pollTimer);
  if (tickTimer) clearInterval(tickTimer);
});
</script>

<template>
  <AsyncState :loading="loading && !summary" :error="error" @retry="retry">
    <template v-if="summary">
      <div v-if="atRisk" class="banner" :class="atRisk.level">
        <span class="badge" :class="`badge-${atRisk.level}`">{{
          atRisk.label
        }}</span>
        {{ atRisk.text }}
      </div>

      <section class="grid gauges">
        <GaugeCard
          v-for="limit in summary.limits"
          :key="limit.window_kind"
          :limit="limit"
          :history="histories[limit.window_kind] ?? []"
          :projected="projectedFor(limit.window_kind)"
        />
        <div v-if="summary.limits.length === 0" class="card muted">
          No limit data yet — the collector reports these once it polls.
        </div>
      </section>

      <section class="grid tiles">
        <StatTile
          label="Burn rate"
          :value="`${formatTokens(Math.round(summary.token_burn_rate_per_min))}/min`"
          sub="trailing 60 minutes"
        />
        <StatTile
          label="Today"
          :value="formatTokens(summary.today.total_tokens)"
          :sub="todayCostSub"
        />
        <StatTile
          label="Plan value"
          :value="valueTile.value"
          :sub="valueTile.sub"
        />
        <StatTile
          label="Cache reads"
          :value="cacheTile.value"
          :sub="cacheTile.sub"
        />
        <StatTile
          v-if="summary.prediction"
          label="Predicted limit"
          :value="timeUntil(summary.prediction.predicted_exhaustion_at)"
          :sub="`resets ${timeUntil(summary.prediction.resets_at)}`"
        />
      </section>

      <section class="card">
        <h3>Today by model (token composition)</h3>
        <template v-if="summary.today.by_model.length">
          <EChart
            :option="modelChart"
            height="320px"
            @legend-select="onLegend"
          />
          <p class="muted small">Bars show composition (each model = 100%).</p>
          <ChartTable
            caption="Today's tokens by model and token type"
            :columns="['Model', ...TOKEN_TABLE_HEADERS]"
            :rows="
              tokenTableRows(summary.today.by_model, (b) => modelLabel(b.key))
            "
          />
        </template>
        <p v-else class="muted">No usage yet today.</p>
      </section>

      <section class="card">
        <div class="head">
          <h3>Live activity</h3>
          <span class="muted small">{{ updatedAgo }}</span>
        </div>
        <ul class="feed">
          <li v-for="row in feed" :key="row.id" class="tabular">
            {{ row.text }}
          </li>
          <li v-if="feed.length === 0" class="muted">Waiting for events…</li>
        </ul>
      </section>
    </template>
  </AsyncState>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.banner {
  padding: 0.7rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1.25rem;
  font-weight: 600;
  border: 1px solid var(--border);
}
.banner .badge {
  margin-right: 0.5rem;
  vertical-align: middle;
}
.banner.good {
  background: color-mix(in srgb, var(--status-good) 12%, transparent);
}
.banner.warning {
  background: color-mix(in srgb, var(--status-warning) 16%, transparent);
}
.banner.critical {
  background: color-mix(in srgb, var(--status-critical) 16%, transparent);
  color: var(--status-critical);
}
.gauges {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
h3 {
  margin: 0 0 1rem;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.head h3 {
  margin: 0 0 0.75rem;
}
.small {
  font-size: 0.8rem;
}
.feed {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.9rem;
}
</style>
