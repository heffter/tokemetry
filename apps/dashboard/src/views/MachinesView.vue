<script setup lang="ts">
// Fleet view: liveness (ticking, so a machine that dies mid-session is caught),
// share of fleet usage, and click-through to a machine's sessions.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { formatTokens } from '@/lib/format';
import { isDown, machineStatus, statusRank } from '@/lib/machines';
import type { MachineSummary } from '@/api/types';

const router = useRouter();
const { loading, error, run, retry } = useAsync();
const machines = ref<MachineSummary[]>([]);
const now = ref(Date.now());
let tickTimer: ReturnType<typeof setInterval> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

// Status level -> a plain-text word and a filled badge class, so status is
// never carried by color alone and stale/offline read distinctly.
const STATUS_WORD: Record<string, string> = {
  online: 'Online',
  recent: 'Recent',
  stale: 'Stale',
  offline: 'Offline',
};
const STATUS_BADGE: Record<string, string> = {
  online: 'badge-good',
  recent: 'badge-good',
  stale: 'badge-warning',
  offline: 'badge-critical',
};

const fleetTokens = computed(() =>
  machines.value.reduce((sum, m) => sum + m.total_tokens, 0)
);
const fleetEvents = computed(() =>
  machines.value.reduce((sum, m) => sum + m.event_count, 0)
);

interface Row extends MachineSummary {
  level: ReturnType<typeof machineStatus>['level'];
  ago: string;
  share: number;
}

const rows = computed<Row[]>(() => {
  const total = fleetTokens.value || 1;
  return machines.value
    .map((m) => {
      const status = machineStatus(m.last_seen, now.value);
      return {
        ...m,
        level: status.level,
        ago: status.ago,
        share: m.total_tokens / total,
      };
    })
    .sort((a, b) => statusRank(a.level) - statusRank(b.level));
});

const downCount = computed(
  () => rows.value.filter((r) => isDown(r.level)).length
);

async function load(): Promise<void> {
  await run(async () => {
    machines.value = await useClient().machines();
    now.value = Date.now();
  });
}

function openSessions(id: string): void {
  void router.push({ path: '/sessions', query: { machine: id } });
}

onMounted(() => {
  void load();
  // Tick advances the "Xm ago" staleness display between polls...
  tickTimer = setInterval(() => {
    now.value = Date.now();
  }, 15000);
  // ...and an actual re-poll refreshes last_seen so a recovered machine turns
  // green without a manual page reload.
  pollTimer = setInterval(() => void load(), 30000);
});

onBeforeUnmount(() => {
  if (tickTimer) clearInterval(tickTimer);
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <AsyncState
    :loading="loading && machines.length === 0"
    :error="error"
    :empty="!loading && machines.length === 0"
    empty-text="No machines enrolled yet."
    @retry="retry"
  >
    <div v-if="downCount > 0" class="banner">
      {{ downCount }} {{ downCount === 1 ? 'machine is' : 'machines are' }}
      offline or stale — check the collector.
    </div>
    <section class="card">
      <h3>Machines</h3>
      <table>
        <thead>
          <tr>
            <th>Machine</th>
            <th>Status</th>
            <th>Platform</th>
            <th>Agent version</th>
            <th class="num">Events</th>
            <th class="num">Tokens</th>
            <!-- Share is meaningless with a single machine (always 100%). -->
            <th v-if="rows.length > 1">Share of fleet</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="m in rows"
            :key="m.id"
            class="clickable"
            @click="openSessions(m.id)"
          >
            <td>{{ m.id }}</td>
            <td>
              <span class="badge" :class="STATUS_BADGE[m.level]">
                <span class="dot" :class="m.level"></span>
                {{ STATUS_WORD[m.level] }}
              </span>
              <span class="ago muted">{{ m.ago }}</span>
            </td>
            <td>{{ m.platform ?? '—' }}</td>
            <td>{{ m.collector_version ?? '—' }}</td>
            <td class="num tabular">{{ m.event_count.toLocaleString() }}</td>
            <td class="num tabular">{{ formatTokens(m.total_tokens) }}</td>
            <td v-if="rows.length > 1">
              <div class="share">
                <div class="bar" :style="{ width: `${m.share * 100}%` }"></div>
                <span class="tabular pct"
                  >{{ (m.share * 100).toFixed(0) }}%</span
                >
              </div>
            </td>
          </tr>
        </tbody>
        <!-- The fleet footer only adds information with more than one machine. -->
        <tfoot v-if="rows.length > 1">
          <tr>
            <td>Fleet ({{ rows.length }})</td>
            <td colspan="3"></td>
            <td class="num tabular">{{ fleetEvents.toLocaleString() }}</td>
            <td class="num tabular">{{ formatTokens(fleetTokens) }}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </section>
  </AsyncState>
</template>

<style scoped>
h3 {
  margin: 0 0 1rem;
}
.banner {
  padding: 0.7rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1.25rem;
  font-weight: 600;
  color: var(--status-critical);
  background: color-mix(in srgb, var(--status-critical) 14%, transparent);
  border: 1px solid var(--border);
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
tfoot td {
  font-weight: 600;
  border-bottom: none;
}
.num {
  text-align: right;
}
.clickable {
  cursor: pointer;
}
.clickable:hover {
  background: var(--gridline);
}
.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.dot.online,
.dot.recent {
  background: var(--status-good);
}
.dot.stale {
  background: var(--status-warning);
}
.dot.offline {
  background: var(--status-critical);
}
.ago {
  margin-left: 0.5rem;
  font-size: 0.85rem;
}
.share {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 120px;
}
.bar {
  height: 8px;
  border-radius: 4px;
  background: var(--series-1);
  flex: 1;
  max-width: 100px;
}
.pct {
  font-size: 0.8rem;
  color: var(--text-muted);
}
</style>
