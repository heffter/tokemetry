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
  formatPct,
  formatTokens,
  modelLabel,
  timeUntil,
} from '@/lib/format';
import { costIsTrustworthy, pricedCoverage } from '@/lib/coverage';
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
const feed = ref<FeedRow[]>([]);
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

const modelChart = computed(() => {
  const models = summary.value?.today.by_model ?? [];
  return stackedTokenBarOption(
    models.map((m) => modelLabel(m.key)),
    models
  );
});

const cacheShare = computed(() =>
  cacheReadShare(summary.value?.today.by_model ?? [])
);

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
      sub: `vs $${c.subscription_monthly_usd}/mo · priced models only`,
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
      <section class="grid gauges">
        <GaugeCard
          v-for="limit in summary.limits"
          :key="limit.window_kind"
          :limit="limit"
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
          :value="formatPct(cacheShare * 100)"
          sub="of today's tokens served from cache"
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
        <EChart :option="modelChart" height="320px" />
        <ChartTable
          caption="Today's tokens by model and token type"
          :columns="['Model', ...TOKEN_TABLE_HEADERS]"
          :rows="
            tokenTableRows(summary.today.by_model, (b) => modelLabel(b.key))
          "
        />
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
