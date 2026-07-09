<script setup lang="ts">
// Front page: live limit gauges, burn rate, prediction, today by model, and a
// live activity feed over the WebSocket stream.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import GaugeCard from '@/components/GaugeCard.vue';
import StatTile from '@/components/StatTile.vue';
import EChart from '@/components/EChart.vue';
import { useClient, useToken } from '@/composables/useApi';
import { barOption } from '@/lib/charts';
import { formatCost, formatTokens, timeUntil } from '@/lib/format';
import type { StreamMessage } from '@/api/types';
import type { SummaryNow } from '@/api/types';

const summary = ref<SummaryNow | null>(null);
const error = ref('');
const feed = ref<string[]>([]);
let socket: WebSocket | null = null;

const modelChart = computed(() => {
  const models = summary.value?.today.by_model ?? [];
  return barOption(
    models.map((m) => m.key),
    models.map((m) => m.total_tokens),
    'tokens'
  );
});

async function load(): Promise<void> {
  try {
    summary.value = await useClient().summaryNow();
  } catch (e) {
    error.value = String(e);
  }
}

function connectStream(): void {
  const { token } = useToken();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(
    `${proto}://${location.host}/api/v1/stream?token=${token.value}`
  );
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data) as StreamMessage;
    feed.value.unshift(
      `${message.machine}: ${message.accepted} ${message.type}`
    );
    feed.value = feed.value.slice(0, 12);
    void load();
  };
}

onMounted(() => {
  void load();
  connectStream();
});

onBeforeUnmount(() => socket?.close());
</script>

<template>
  <div v-if="error" class="card">{{ error }}</div>
  <template v-else-if="summary">
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
        :sub="`${formatCost(summary.today.cost_usd)} equivalent`"
      />
      <StatTile
        v-if="summary.prediction"
        label="Predicted limit"
        :value="timeUntil(summary.prediction.predicted_exhaustion_at)"
        :sub="`resets ${timeUntil(summary.prediction.resets_at)}`"
      />
    </section>

    <section class="card">
      <h3>Today by model</h3>
      <EChart :option="modelChart" height="300px" />
    </section>

    <section class="card">
      <h3>Live activity</h3>
      <ul class="feed">
        <li v-for="(line, index) in feed" :key="index" class="tabular">
          {{ line }}
        </li>
        <li v-if="feed.length === 0" class="muted">Waiting for events…</li>
      </ul>
    </section>
  </template>
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
