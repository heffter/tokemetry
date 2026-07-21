<script setup lang="ts">
// Provider-neutral limit snapshots (FR-UI-010/012), backed by /api/v2/limits.
// Sections render per provider for every provider with limit data; the latest
// snapshot per window is shown with its utilization, official-vs-estimated
// provenance, and reset. Window labels resolve through windowLabel, which falls
// back to the Anthropic seed until the provider window registry (Task 69)
// supplies labels -- so Anthropic's 5-hour and weekly windows read as today
// (FR-UI-014) and other providers' windows appear by their raw kind meanwhile.
import { computed, onMounted, ref } from 'vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  formatDateTime,
  formatPct,
  timeUntil,
  utilizationStatus,
  windowLabel,
} from '@/lib/format';
import {
  clampRangeDays,
  dayEndIso,
  dayStartIso,
  presetRange,
} from '@/lib/filters';
import { windowLabelsFrom } from '@/lib/windows';
import type { LimitSnapshotV2, ProviderV2 } from '@/api/types-v2';

const MAX_RANGE_DAYS = 365;

const { loading, error, run, retry } = useAsync();
const snapshots = ref<LimitSnapshotV2[]>([]);
const providers = ref<ProviderV2[]>([]);

const providerName = computed(() => {
  const map = new Map(providers.value.map((p) => [p.id, p.display_name]));
  return (id: string): string => map.get(id) ?? id;
});
// Window labels resolved from the provider registry (FR-LIMIT-012), falling
// back to the Anthropic seed inside windowLabel for kinds the registry omits.
const windowLabels = computed(() => windowLabelsFrom(providers.value));

// The latest snapshot per (provider, window_kind), grouped by provider.
const byProvider = computed(() => {
  const latest = new Map<string, LimitSnapshotV2>();
  for (const snap of snapshots.value) {
    const key = snap.provider + ' ' + snap.window_kind;
    const seen = latest.get(key);
    if (!seen || new Date(snap.ts).getTime() > new Date(seen.ts).getTime()) {
      latest.set(key, snap);
    }
  }
  const groups = new Map<string, LimitSnapshotV2[]>();
  for (const snap of latest.values()) {
    const list = groups.get(snap.provider) ?? [];
    list.push(snap);
    groups.set(snap.provider, list);
  }
  return [...groups.entries()]
    .map(([provider, windows]) => ({
      provider,
      windows: windows.sort((a, b) =>
        a.window_kind.localeCompare(b.window_kind)
      ),
    }))
    .sort((a, b) => a.provider.localeCompare(b.provider));
});

function pct(value: string): number {
  return Number(value);
}
function statusVar(value: string): string {
  return 'var(--status-' + utilizationStatus(pct(value)) + ')';
}

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const all = presetRange('all');
    const clamped = clampRangeDays(all.from, all.to, MAX_RANGE_DAYS);
    const from = dayStartIso(clamped.from);
    // Inclusive end-of-day so today's snapshots (the only ones a freshly wired
    // limit source has) are not excluded by a start-of-day bound.
    const to = dayEndIso(clamped.to);
    const rows: LimitSnapshotV2[] = [];
    let cursor: string | undefined;
    for (let page = 0; page < 20; page += 1) {
      const res = await client.v2Limits({ from, to, limit: 200, cursor });
      rows.push(...res.limits);
      if (!res.next_cursor) break;
      cursor = res.next_cursor;
    }
    snapshots.value = rows;
    providers.value = await client.v2Providers().catch(() => []);
  });
}

onMounted(() => {
  void load();
});
</script>

<template>
  <AsyncState
    :loading="loading && snapshots.length === 0"
    :error="error"
    :empty="!loading && snapshots.length === 0"
    empty-text="No limit snapshots yet — providers report these once their limit sources are wired."
    @retry="retry"
  >
    <section v-for="group in byProvider" :key="group.provider" class="card">
      <h3>{{ providerName(group.provider) }} limits</h3>
      <div class="grid windows">
        <div v-for="w in group.windows" :key="w.window_kind" class="window">
          <div class="wtop">
            <span class="wlabel">{{
              windowLabel(w.window_kind, windowLabels)
            }}</span>
            <span
              class="pct tabular"
              :style="{ color: statusVar(w.utilization_pct) }"
            >
              {{ formatPct(pct(w.utilization_pct)) }}
            </span>
          </div>
          <div class="track">
            <div
              class="fill"
              :style="{
                width: Math.min(100, pct(w.utilization_pct)) + '%',
                background: statusVar(w.utilization_pct),
              }"
            ></div>
          </div>
          <div class="wfoot muted">
            <span class="badge" :class="'prov-' + w.provenance">{{
              w.provenance
            }}</span>
            <span :title="w.resets_at ? formatDateTime(w.resets_at) : ''">
              resets {{ w.resets_at ? timeUntil(w.resets_at) : '—' }}
            </span>
          </div>
        </div>
      </div>
    </section>
  </AsyncState>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0 0 1rem;
}
.windows {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.window {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: var(--radius, 10px);
}
.wtop {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
}
.pct {
  font-size: 1.05rem;
}
.track {
  height: 9px;
  border-radius: 5px;
  background: var(--gridline);
  overflow: hidden;
}
.fill {
  height: 100%;
  border-radius: 5px;
}
.wfoot {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
  align-items: center;
}
.badge {
  font-size: 0.66rem;
  font-weight: 600;
  text-transform: uppercase;
  padding: 0.05rem 0.35rem;
  border-radius: 5px;
  border: 1px solid var(--border);
}
.prov-estimated {
  color: var(--status-warning);
}
</style>
